#!/usr/bin/env python3
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


BASE_URL = "https://capcut-mate.jcaigc.cn/openapi/capcut-mate/v1"
MICROSECONDS_PER_SECOND = 1_000_000
SEGMENT_SECONDS = 5
CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720
SOURCE_VIDEO_WIDTH = 1280
SOURCE_VIDEO_HEIGHT = 720
BACKGROUND_ALPHA = 0.7
FOREGROUND_SCALE = 1.0
CAPTION_FONT_SIZE = 8
CAPTION_TEXT_COLOR = "#f7f1df"
CAPTION_BORDER_COLOR = "#000000"
CAPTION_FONT = "江湖体"
CAPTION_LINE_SPACING = 10
CAPTION_TRANSFORM_Y = -280


def post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{BASE_URL}/{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{path} failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{path} failed with network error: {exc.reason}") from exc

    return json.loads(raw) if raw else {}


def load_links(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    links = data.get("links", [])
    if not links:
        raise ValueError(f"No links found in {path}")
    for item in links:
        if not item.get("video_url"):
            raise ValueError(f"Missing video_url for link item {item.get('id')}")
    return sorted(links, key=lambda item: item["id"])


def load_timing_plan(
    path: Path,
    link_ids: list[str],
) -> tuple[list[dict[str, int]], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    scenes = data.get("scenes") if isinstance(data, dict) else None
    if not isinstance(scenes, list) or not scenes:
        raise ValueError(f"Timing plan has no scenes: {path}")
    if len(scenes) != len(link_ids):
        raise ValueError(
            f"Timing scene count {len(scenes)} does not match video count {len(link_ids)}"
        )

    timelines = []
    for index, (scene, link_id) in enumerate(zip(scenes, link_ids), start=1):
        scene_id = str(scene.get("id", f"{index:02d}"))
        if scene_id != str(link_id):
            raise ValueError(
                f"Timing scene ID {scene_id} does not match video link ID {link_id}"
            )
        start = scene.get("start_us")
        end = scene.get("end_us")
        if start is None or end is None:
            start = round(float(scene["start"]) * MICROSECONDS_PER_SECOND)
            end = round(float(scene["end"]) * MICROSECONDS_PER_SECOND)
        start = int(start)
        end = int(end)
        if start < 0 or end <= start:
            raise ValueError(f"Invalid timing range for scene {scene_id}: {start}-{end}")
        if timelines and start != timelines[-1]["end"]:
            raise ValueError(
                f"Timing plan must be contiguous: scene {scene_id} starts at {start}, "
                f"previous scene ends at {timelines[-1]['end']}"
            )
        timelines.append({"start": start, "end": end})

    if timelines[0]["start"] != 0:
        raise ValueError("Timing plan must start at zero")
    return timelines, data


def caption_items(path: Path) -> list[Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("captions"), list):
        return data["captions"]
    if isinstance(data, dict) and isinstance(data.get("scenes"), list):
        return data["scenes"]
    raise ValueError(f"Unsupported captions format: {path}")


def load_captions(
    path: Path,
    scene_timelines: list[dict[str, int]],
) -> list[dict[str, Any]]:
    items = caption_items(path)
    if not items:
        raise ValueError("Caption list must not be empty")
    timed_flags = [
        isinstance(item, dict)
        and (
            (item.get("start_us") is not None and item.get("end_us") is not None)
            or (item.get("start") is not None and item.get("end") is not None)
        )
        for item in items
    ]
    if any(timed_flags) and not all(timed_flags):
        raise ValueError("Captions must either all include timestamps or all omit them")

    captions = []
    if all(timed_flags):
        for index, item in enumerate(items, start=1):
            text = str(item.get("text") or item.get("caption") or item.get("subtitle") or "").strip()
            if not text:
                raise ValueError(f"Caption {index} has no text")
            start = item.get("start_us")
            end = item.get("end_us")
            if start is None or end is None:
                start = round(float(item["start"]) * MICROSECONDS_PER_SECOND)
                end = round(float(item["end"]) * MICROSECONDS_PER_SECOND)
            start = int(start)
            end = int(end)
            if start < 0 or end <= start:
                raise ValueError(f"Caption {index} has an invalid time range")
            if end > scene_timelines[-1]["end"]:
                raise ValueError(
                    f"Caption {index} ends after the video timeline: {end} > "
                    f"{scene_timelines[-1]['end']}"
                )
            captions.append({"text": text, "start": start, "end": end})
        return captions

    if len(items) != len(scene_timelines):
        raise ValueError(
            f"Caption count {len(items)} does not match video count {len(scene_timelines)}"
        )
    for item, timeline in zip(items, scene_timelines):
        if isinstance(item, dict):
            text = str(
                item.get("text")
                or item.get("caption")
                or item.get("subtitle")
                or item.get("title")
                or ""
            ).strip()
        else:
            text = str(item).strip()
        if not text:
            raise ValueError("Caption text must not be empty")
        captions.append({"text": text, **timeline})
    return captions


def build_timeline(count: int, segment_seconds: int) -> list[dict[str, int]]:
    segment_us = segment_seconds * MICROSECONDS_PER_SECOND
    return [
        {"start": index * segment_us, "end": (index + 1) * segment_us}
        for index in range(count)
    ]


def write_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Jianying draft from Seedance video links.")
    parser.add_argument("--links-file", type=Path, required=True)
    parser.add_argument("--record-file", type=Path, required=True)
    parser.add_argument("--captions-file", type=Path, required=True)
    parser.add_argument("--timing-file", type=Path, help="Voice-first timing plan with per-scene start_us/end_us values.")
    parser.add_argument("--segment-seconds", type=int, default=SEGMENT_SECONDS)
    parser.add_argument("--canvas-width", type=int, default=CANVAS_WIDTH)
    parser.add_argument("--canvas-height", type=int, default=CANVAS_HEIGHT)
    parser.add_argument("--source-width", type=int, default=SOURCE_VIDEO_WIDTH)
    parser.add_argument("--source-height", type=int, default=SOURCE_VIDEO_HEIGHT)
    parser.add_argument("--foreground-scale", type=float, default=FOREGROUND_SCALE, help="Foreground video scale. Keep 1.0 by default; use --fit-to-canvas only when explicitly desired.")
    parser.add_argument("--fit-to-canvas", action="store_true", help="Scale foreground videos to canvas width. This may upscale 1280x720 clips to 1.5x on a 1920x1080 canvas.")
    parser.add_argument("--caption-font-size", type=int, default=CAPTION_FONT_SIZE)
    parser.add_argument("--caption-font", default=CAPTION_FONT)
    parser.add_argument("--caption-line-spacing", type=int, default=CAPTION_LINE_SPACING)
    parser.add_argument("--caption-text-color", default=CAPTION_TEXT_COLOR)
    parser.add_argument("--caption-border-color", default=CAPTION_BORDER_COLOR)
    parser.add_argument("--caption-transform-y", type=int, default=CAPTION_TRANSFORM_Y)
    parser.add_argument("--audio-url", help="Public HTTPS narration URL. Required unless --no-audio is used.")
    parser.add_argument("--audio-duration-us", type=int, help="Actual audio duration in microseconds. Defaults to the full video timeline.")
    parser.add_argument("--audio-volume", type=float, default=1.0)
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--optimized-layout", action="store_true", help="Use a cover background layer and smaller warmer captions.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.no_audio and not args.audio_url:
        raise ValueError("--audio-url is required unless --no-audio is used")
    links = load_links(args.links_file)
    if args.timing_file:
        timelines, timing_plan = load_timing_plan(
            args.timing_file,
            [str(link["id"]) for link in links],
        )
        timing_mode = "voice-first-dynamic"
    else:
        timelines = build_timeline(len(links), args.segment_seconds)
        timing_plan = {}
        timing_mode = "fixed-segments"
    captions = load_captions(args.captions_file, timelines)
    total_duration = timelines[-1]["end"] if timelines else 0
    planned_audio_duration = timing_plan.get("audio_duration_us") if timing_plan else None
    audio_duration = min(
        args.audio_duration_us or planned_audio_duration or total_duration,
        total_duration,
    )
    if not args.no_audio and audio_duration <= 0:
        raise ValueError("Audio duration must be positive")
    foreground_scale = args.canvas_width / args.source_width if args.fit_to_canvas else args.foreground_scale
    scale_to_cover_height = args.canvas_height / args.source_height

    record: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "canvas": {"width": args.canvas_width, "height": args.canvas_height},
        "layout": "optimized-background" if args.optimized_layout else ("fit-width" if args.fit_to_canvas else "actual-size"),
        "foreground_scale": foreground_scale,
        "timing_mode": timing_mode,
        "timing_file": str(args.timing_file) if args.timing_file else None,
        "segment_seconds": args.segment_seconds if not args.timing_file else None,
        "total_duration_us": total_duration,
        "steps": [],
    }

    print(f"[create_draft] {args.canvas_width}x{args.canvas_height}")
    create_payload = {"width": args.canvas_width, "height": args.canvas_height}
    create_response = post_json("create_draft", create_payload)
    draft_url: Optional[str] = create_response.get("draft_url")
    if not draft_url:
        raise RuntimeError(f"create_draft response missing draft_url: {create_response}")
    record["steps"].append({"name": "create_draft", "request": create_payload, "response": create_response})

    video_infos = []
    for link, timeline in zip(links, timelines):
        duration = timeline["end"] - timeline["start"]
        video_infos.append(
            {
                "video_url": link["video_url"],
                "width": args.source_width,
                "height": args.source_height,
                "start": timeline["start"],
                "end": timeline["end"],
                "duration": duration,
                "volume": 0.0,
            }
        )

    if args.optimized_layout:
        print(f"[add_videos] {len(video_infos)} background clips, total {total_duration / MICROSECONDS_PER_SECOND:.0f}s")
        add_background_payload = {
            "draft_url": draft_url,
            "video_infos": json.dumps(video_infos, ensure_ascii=False),
            "scene_timelines": timelines,
            "alpha": BACKGROUND_ALPHA,
            "scale_x": scale_to_cover_height,
            "scale_y": scale_to_cover_height,
            "transform_x": 0,
            "transform_y": 0,
        }
        add_background_response = post_json("add_videos", add_background_payload)
        draft_url = add_background_response.get("draft_url", draft_url)
        record["steps"].append({"name": "add_videos_background", "request": add_background_payload, "response": add_background_response})

    print(f"[add_videos] {len(video_infos)} foreground clips, total {total_duration / MICROSECONDS_PER_SECOND:.0f}s")
    add_videos_payload = {
        "draft_url": draft_url,
        "video_infos": json.dumps(video_infos, ensure_ascii=False),
        "scene_timelines": timelines,
        "alpha": 1.0,
        "scale_x": foreground_scale,
        "scale_y": foreground_scale,
        "transform_x": 0,
        "transform_y": 0,
    }
    add_videos_response = post_json("add_videos", add_videos_payload)
    draft_url = add_videos_response.get("draft_url", draft_url)
    record["steps"].append({"name": "add_videos_foreground", "request": add_videos_payload, "response": add_videos_response})

    if not args.no_audio:
        audio_infos = [
            {
                "audio_url": args.audio_url,
                "start": 0,
                "end": audio_duration,
                "duration": audio_duration,
                "volume": args.audio_volume,
            }
        ]
        print(f"[add_audios] audio duration {audio_duration / MICROSECONDS_PER_SECOND:.3f}s")
        add_audios_payload = {
            "draft_url": draft_url,
            "audio_infos": json.dumps(audio_infos, ensure_ascii=False),
        }
        add_audios_response = post_json("add_audios", add_audios_payload)
        draft_url = add_audios_response.get("draft_url", draft_url)
        record["steps"].append({"name": "add_audios", "request": add_audios_payload, "response": add_audios_response})

    caption_infos = [
        {
            "start": caption["start"],
            "end": caption["end"],
            "text": caption["text"],
            "font_size": args.caption_font_size,
        }
        for caption in captions
    ]
    print(f"[add_captions] {len(caption_infos)} captions")
    add_captions_payload = {
        "draft_url": draft_url,
        "captions": json.dumps(caption_infos, ensure_ascii=False),
        "border_color": args.caption_border_color,
        "font": args.caption_font,
        "font_size": args.caption_font_size,
        "line_spacing": args.caption_line_spacing,
        "text_color": args.caption_text_color,
        "transform_y": args.caption_transform_y,
        "alignment": 1,
        "style_text": False,
        "has_shadow": True,
        "shadow_info": {
            "shadow_color": "#000000",
            "shadow_alpha": 0.75,
            "shadow_diffuse": 10,
            "shadow_distance": 3,
            "shadow_angle": -45,
        },
    }
    add_captions_response = post_json("add_captions", add_captions_payload)
    draft_url = add_captions_response.get("draft_url", draft_url)
    record["steps"].append({"name": "add_captions", "request": add_captions_payload, "response": add_captions_response})

    print("[save_draft]")
    save_payload = {"draft_url": draft_url}
    save_response = post_json("save_draft", save_payload)
    draft_url = save_response.get("draft_url", draft_url)
    record["steps"].append({"name": "save_draft", "request": save_payload, "response": save_response})
    record["draft_url"] = draft_url

    write_record(args.record_file, record)
    print(f"[done] draft saved")
    print(f"[record] {args.record_file}")
    print(draft_url)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
