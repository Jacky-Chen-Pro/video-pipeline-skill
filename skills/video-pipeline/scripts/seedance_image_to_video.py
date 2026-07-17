#!/usr/bin/env python3
import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seedance-1-5-pro-251215"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "expired"}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def portable_path(path: Path, root: Path) -> str:
    """Prefer a workspace-relative path so records can be shared across machines."""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def resolve_manifest_relative_path(root: Path, manifest_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path

    manifest_relative = manifest_path.parent / path
    root_relative = root / path
    if manifest_relative.exists() or not root_relative.exists():
        return manifest_relative
    return root_relative


def image_to_data_url(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type:
        mime_type = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def request_json(url: str, api_key: str, method: str = "GET", payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    data = None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from exc

    if not body:
        return {}
    return json.loads(body)


def download_file(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "makevideo-seedance/1.0"})
    with urllib.request.urlopen(request, timeout=300) as response:
        output_path.write_bytes(response.read())


def load_manifest(root: Path, manifest_path: Path) -> list[dict[str, Any]]:
    scenes = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(scenes, list) or not scenes:
        raise ValueError(f"{manifest_path} must contain a non-empty scene list")

    for scene in scenes:
        for required in ("id", "title", "image", "prompt"):
            if required not in scene:
                raise ValueError(f"Scene missing required field {required}: {scene}")
        image_path = resolve_manifest_relative_path(root, manifest_path, scene["image"])
        if not image_path.exists():
            raise FileNotFoundError(f"Missing image for scene {scene['id']}: {image_path}")
    return scenes


def build_payload(
    root: Path,
    manifest_path: Path,
    scene: dict[str, Any],
    model: str,
    resolution: str,
    ratio: str,
    generate_audio: bool,
    watermark: bool,
    return_last_frame: bool,
) -> dict[str, Any]:
    image_path = resolve_manifest_relative_path(root, manifest_path, scene["image"])
    duration = int(scene.get("duration", 5))
    payload: dict[str, Any] = {
        "model": model,
        "content": [
            {
                "type": "text",
                "text": scene["prompt"],
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": image_to_data_url(image_path),
                },
                "role": "first_frame",
            },
        ],
        "resolution": scene.get("resolution", resolution),
        "ratio": scene.get("ratio", ratio),
        "duration": duration,
        "generate_audio": generate_audio,
        "watermark": watermark,
        "return_last_frame": return_last_frame,
    }
    if "seed" in scene:
        payload["seed"] = int(scene["seed"])
    return payload


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"tasks": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def infer_project_name(manifest_path: Path) -> str:
    if manifest_path.parent.name == "assets" and manifest_path.parent.parent.name:
        return manifest_path.parent.parent.name
    return manifest_path.parent.name


def epoch_to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def scrub_temporary_urls(task: dict[str, Any]) -> None:
    result = task.get("last_result")
    if not isinstance(result, dict):
        return
    content = result.get("content")
    if not isinstance(content, dict):
        return
    for key in ("video_url", "last_frame_url", "file_url"):
        if content.get(key):
            content[key] = "<downloaded>"


def export_generation_records(args: argparse.Namespace, scenes: list[dict[str, Any]], state: dict[str, Any], include_links: bool) -> None:
    tasks = state.setdefault("tasks", {})
    fetched_at = datetime.now(timezone.utc)
    project = args.project or infer_project_name(args.manifest)
    scene_records = []
    link_records = []

    for scene in scenes:
        scene_id = scene["id"]
        task = tasks.get(scene_id, {})
        result = task.get("last_result") if isinstance(task.get("last_result"), dict) else {}
        content = result.get("content") if isinstance(result.get("content"), dict) else {}
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else None

        scene_records.append(
            {
                "id": scene_id,
                "title": scene["title"],
                "source_image": str(scene["image"]),
                "prompt": scene["prompt"],
                "request": {
                    "model": result.get("model", args.model),
                    "resolution": result.get("resolution", scene.get("resolution", args.resolution)),
                    "ratio": result.get("ratio", scene.get("ratio", args.ratio)),
                    "duration": result.get("duration", scene.get("duration")),
                    "generate_audio": result.get("generate_audio", args.generate_audio),
                    "watermark": args.watermark,
                    "return_last_frame": args.return_last_frame,
                    "role": "first_frame",
                },
                "result": {
                    "task_id": task.get("task_id"),
                    "status": task.get("status"),
                    "seed": result.get("seed"),
                    "frames_per_second": result.get("framespersecond") or result.get("framesPerSecond"),
                    "created_at": epoch_to_iso(result.get("created_at")),
                    "updated_at": epoch_to_iso(result.get("updated_at")),
                    "usage": usage,
                    "local_video_path": task.get("video_path"),
                    "local_last_frame_path": task.get("last_frame_path"),
                },
            }
        )

        if include_links:
            video_url = content.get("video_url")
            last_frame_url = content.get("last_frame_url")
            if video_url or last_frame_url:
                link_records.append(
                    {
                        "id": scene_id,
                        "title": scene["title"],
                        "task_id": task.get("task_id"),
                        "status": task.get("status"),
                        "video_url": video_url,
                        "last_frame_url": last_frame_url,
                    }
                )

    record = {
        "project": project,
        "provider": "volcengine-ark",
        "api_base_url": args.base_url,
        "model": args.model,
        "generated_at": utc_now_iso(),
        "notes": [
            "This file is the durable generation record: prompts, request settings, task IDs, seeds, usage, and local output paths.",
            "Temporary signed download URLs are saved separately in generation-links.private.json when available.",
        ],
        "scenes": scene_records,
    }
    args.record_file.parent.mkdir(parents=True, exist_ok=True)
    args.record_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    if include_links:
        private_record = {
            "project": project,
            "provider": "volcengine-ark",
            "fetched_at": fetched_at.isoformat(),
            "expected_expiration_at": (fetched_at + timedelta(hours=24)).isoformat(),
            "note": "These are temporary signed URLs returned by the video generation API. Treat this file as private; URLs may expire after about 24 hours.",
            "links": link_records,
        }
        args.link_record_file.parent.mkdir(parents=True, exist_ok=True)
        args.link_record_file.write_text(json.dumps(private_record, ensure_ascii=False, indent=2), encoding="utf-8")


def submit_missing_tasks(args: argparse.Namespace, root: Path, api_key: str, scenes: list[dict[str, Any]], state: dict[str, Any]) -> None:
    create_url = f"{args.base_url.rstrip('/')}/contents/generations/tasks"
    tasks = state.setdefault("tasks", {})

    for scene in scenes:
        scene_id = scene["id"]
        current = tasks.get(scene_id, {})
        if current.get("task_id") and not args.force:
            print(f"[skip] scene {scene_id} already has task {current['task_id']}")
            continue

        payload = build_payload(
            root=root,
            manifest_path=args.manifest,
            scene=scene,
            model=args.model,
            resolution=args.resolution,
            ratio=args.ratio,
            generate_audio=args.generate_audio,
            watermark=args.watermark,
            return_last_frame=args.return_last_frame,
        )
        print(f"[submit] scene {scene_id} {scene['title']}")
        response = request_json(create_url, api_key=api_key, method="POST", payload=payload)
        task_id = response.get("id")
        if not task_id:
            raise RuntimeError(f"Create task response missing id for scene {scene_id}: {response}")
        tasks[scene_id] = {
            "title": scene["title"],
            "image": scene["image"],
            "task_id": task_id,
            "status": "submitted",
            "created_local_at": int(time.time()),
        }
        write_state(args.state_file, state)
        print(f"[created] scene {scene_id} task {task_id}")


def poll_tasks(args: argparse.Namespace, api_key: str, scenes: list[dict[str, Any]], state: dict[str, Any]) -> None:
    tasks = state.setdefault("tasks", {})
    pending_ids = [scene["id"] for scene in scenes if tasks.get(scene["id"], {}).get("task_id")]
    deadline = time.time() + args.timeout

    while pending_ids:
        still_pending: list[str] = []
        for scene_id in pending_ids:
            task = tasks[scene_id]
            task_id = task["task_id"]
            query_url = f"{args.base_url.rstrip('/')}/contents/generations/tasks/{task_id}"
            result = request_json(query_url, api_key=api_key, method="GET")
            status = result.get("status", "unknown")
            task["status"] = status
            task["updated_local_at"] = int(time.time())
            task["last_result"] = result

            if status in TERMINAL_STATUSES:
                print(f"[{status}] scene {scene_id} task {task_id}")
            else:
                print(f"[poll] scene {scene_id} task {task_id}: {status}")
                still_pending.append(scene_id)
        write_state(args.state_file, state)

        if not still_pending:
            break
        if time.time() >= deadline:
            raise TimeoutError(f"Timed out with pending scenes: {', '.join(still_pending)}")
        pending_ids = still_pending
        time.sleep(args.poll_interval)


def download_finished(args: argparse.Namespace, scenes: list[dict[str, Any]], state: dict[str, Any]) -> None:
    tasks = state.setdefault("tasks", {})
    scene_by_id = {scene["id"]: scene for scene in scenes}

    for scene_id, task in tasks.items():
        if task.get("status") != "succeeded":
            continue
        result = task.get("last_result") or {}
        content = result.get("content") or {}
        scene = scene_by_id.get(scene_id, {})
        title = scene.get("title", task.get("title", scene_id))

        video_url = content.get("video_url")
        if video_url and not task.get("video_path"):
            video_path = args.out_dir / f"{scene_id}-{title}.mp4"
            print(f"[download] scene {scene_id} video")
            download_file(video_url, video_path)
            task["video_path"] = portable_path(video_path, Path.cwd())

        last_frame_url = content.get("last_frame_url")
        if last_frame_url and not task.get("last_frame_path"):
            last_frame_path = args.out_dir / "last_frames" / f"{scene_id}-{title}.png"
            print(f"[download] scene {scene_id} last frame")
            download_file(last_frame_url, last_frame_path)
            task["last_frame_path"] = portable_path(last_frame_path, Path.cwd())

        scrub_temporary_urls(task)

    write_state(args.state_file, state)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate image-to-video clips with Doubao Seedance from a scene manifest.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env.local"))
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, help="Defaults to <out-dir>/tasks.json")
    parser.add_argument("--record-file", type=Path, help="Defaults to <out-dir>/generation-record.json")
    parser.add_argument("--link-record-file", type=Path, help="Defaults to <out-dir>/generation-links.private.json")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--project", help="Project name to write into generation records. Defaults to the video folder name when the manifest is videos/<project>/assets/seedance_scenes.json.")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--ratio", default="16:9")
    parser.add_argument("--poll-interval", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--submit-only", action="store_true")
    parser.add_argument("--poll-only", action="store_true")
    parser.add_argument("--force", action="store_true", help="Create new tasks even when task IDs already exist.")
    parser.add_argument("--generate-audio", action="store_true", help="Ask Seedance to generate synchronized audio.")
    parser.add_argument("--watermark", action="store_true", help="Ask Seedance to add an AI Generated watermark.")
    parser.add_argument("--no-return-last-frame", dest="return_last_frame", action="store_false")
    parser.set_defaults(return_last_frame=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path.cwd()
    args.manifest = resolve_path(root, str(args.manifest))
    args.env_file = resolve_path(root, str(args.env_file))
    args.out_dir = resolve_path(root, str(args.out_dir))
    args.state_file = resolve_path(root, str(args.state_file)) if args.state_file else args.out_dir / "tasks.json"
    args.record_file = resolve_path(root, str(args.record_file)) if args.record_file else args.out_dir / "generation-record.json"
    args.link_record_file = (
        resolve_path(root, str(args.link_record_file))
        if args.link_record_file
        else args.out_dir / "generation-links.private.json"
    )

    load_env_file(args.env_file)
    api_key = os.environ.get("ARK_API_KEY")
    if not api_key:
        raise RuntimeError("ARK_API_KEY is not set. Put it in .env.local or export it in the shell.")

    scenes = load_manifest(root, args.manifest)
    state = read_state(args.state_file)

    if not args.poll_only:
        submit_missing_tasks(args, root, api_key, scenes, state)
    if args.submit_only:
        return

    state = read_state(args.state_file)
    poll_tasks(args, api_key, scenes, state)
    state = read_state(args.state_file)
    export_generation_records(args, scenes, state, include_links=True)
    download_finished(args, scenes, state)
    state = read_state(args.state_file)
    export_generation_records(args, scenes, state, include_links=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
