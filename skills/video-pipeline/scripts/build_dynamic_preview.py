#!/usr/bin/env python3
"""Build a local preview whose clip lengths follow a voice-first timing plan."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


MICROSECONDS_PER_SECOND = 1_000_000


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def probe_media(path: Path) -> dict[str, Any]:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ]
    )
    data = json.loads(result.stdout)
    data["duration_seconds"] = float(data["format"]["duration"])
    return data


def load_timing_plan(path: Path) -> tuple[list[dict[str, Any]], float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError(f"Timing plan has no scenes: {path}")
    total = float(data.get("total_duration_seconds") or 0)
    if total <= 0:
        total = max(float(scene["end_us"]) for scene in scenes) / MICROSECONDS_PER_SECOND
    return scenes, total


def load_captions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    captions = data.get("captions") if isinstance(data, dict) else data
    if not isinstance(captions, list) or not captions:
        raise ValueError(f"Caption file has no captions: {path}")
    normalized = []
    for index, caption in enumerate(captions, start=1):
        if not isinstance(caption, dict) or not str(caption.get("text", "")).strip():
            raise ValueError(f"Caption {index} is missing text")
        start = caption.get("start_us")
        end = caption.get("end_us")
        if start is None or end is None:
            start = round(float(caption["start"]) * MICROSECONDS_PER_SECOND)
            end = round(float(caption["end"]) * MICROSECONDS_PER_SECOND)
        if int(end) <= int(start):
            raise ValueError(f"Caption {index} has an invalid time range")
        normalized.append({**caption, "start_us": int(start), "end_us": int(end)})
    return normalized


def find_clip(clips_dir: Path, scene_id: str) -> Path:
    matches = sorted(
        path
        for path in clips_dir.glob(f"{scene_id}-*.mp4")
        if path.is_file() and not path.name.startswith("._")
    )
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one clip for scene {scene_id} in {clips_dir}, found {len(matches)}"
        )
    return matches[0]


def srt_timestamp(microseconds: int) -> str:
    milliseconds = max(0, round(microseconds / 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def srt_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def write_srt(path: Path, captions: list[dict[str, Any]]) -> None:
    blocks = []
    for index, caption in enumerate(captions, start=1):
        blocks.append(
            f"{index}\n{srt_timestamp(caption['start_us'])} --> "
            f"{srt_timestamp(caption['end_us'])}\n{srt_text(str(caption['text']))}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def escape_filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def ffmpeg_has_filter(name: str) -> bool:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        check=False,
        capture_output=True,
        text=True,
    )
    return re.search(rf"^\s*\S+\s+{re.escape(name)}\s", result.stdout, re.MULTILINE) is not None


def resolve_font_file(explicit: Optional[Path]) -> Path:
    candidates = [
        explicit,
        Path.cwd() / "assets/fonts/NotoSansCJK-Regular.ttc",
        Path.cwd() / "assets/fonts/Hiragino Sans GB.ttc",
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    raise FileNotFoundError(
        "No Chinese font file found; pass --font-file or install a CJK font"
    )


def wrap_text(draw: Any, text: str, font: Any, max_width: int) -> list[str]:
    lines = []
    current = ""
    for character in text:
        candidate = current + character
        width = draw.textbbox((0, 0), candidate, font=font, stroke_width=2)[2]
        if current and width > max_width:
            lines.append(current)
            current = character
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def render_caption_overlays(
    captions: list[dict[str, Any]],
    output_dir: Path,
    width: int,
    font_file: Path,
    font_size: int,
) -> list[Path]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is required to render captions when FFmpeg lacks libass"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(font_file), font_size)
    probe = Image.new("RGBA", (width, 200), (0, 0, 0, 0))
    probe_draw = ImageDraw.Draw(probe)
    overlay_paths = []

    for index, caption in enumerate(captions, start=1):
        lines = wrap_text(
            probe_draw,
            srt_text(str(caption["text"])),
            font,
            width - 96,
        )
        line_height = round(font_size * 1.35)
        height = max(64, line_height * len(lines) + 20)
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for line_index, line in enumerate(lines):
            box = draw.textbbox((0, 0), line, font=font, stroke_width=2)
            text_width = box[2] - box[0]
            x = (width - text_width) // 2
            y = 8 + line_index * line_height
            draw.text(
                (x, y),
                line,
                font=font,
                fill=(247, 241, 223, 255),
                stroke_width=2,
                stroke_fill=(20, 20, 20, 235),
            )
        path = output_dir / f"caption-{index:02d}.png"
        image.save(path)
        overlay_paths.append(path)
    return overlay_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a dynamically timed local MP4 preview.")
    parser.add_argument("--timing-file", type=Path, required=True)
    parser.add_argument("--captions-file", type=Path, required=True)
    parser.add_argument("--clips-dir", type=Path, required=True)
    parser.add_argument("--audio-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--srt-output", type=Path)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--crf", type=int, default=18)
    parser.add_argument("--preset", default="medium")
    parser.add_argument("--font-name", default="Noto Sans CJK SC")
    parser.add_argument("--font-file", type=Path)
    parser.add_argument("--font-size", type=int, default=34)
    parser.add_argument("--margin-v", type=int, default=46)
    parser.add_argument("--no-burn-captions", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        scenes, total_duration = load_timing_plan(args.timing_file)
        captions = load_captions(args.captions_file)
        clips = [find_clip(args.clips_dir, str(scene["id"])) for scene in scenes]
        clip_probes = [probe_media(clip) for clip in clips]
        audio_probe = probe_media(args.audio_file)
        audio_duration = audio_probe["duration_seconds"]
        if abs(audio_duration - total_duration) > 0.08:
            raise ValueError(
                f"Audio duration {audio_duration:.3f}s differs from timing plan "
                f"{total_duration:.3f}s by more than 80ms"
            )

        args.output.parent.mkdir(parents=True, exist_ok=True)
        srt_path = args.srt_output or args.output.with_suffix(".srt")
        write_srt(srt_path, captions)

        use_libass = not args.no_burn_captions and ffmpeg_has_filter("subtitles")
        overlay_paths: list[Path] = []
        if not args.no_burn_captions and not use_libass:
            font_file = resolve_font_file(args.font_file)
            overlay_paths = render_caption_overlays(
                captions,
                args.output.parent / f"{args.output.stem}-caption-overlays",
                args.width,
                font_file,
                args.font_size,
            )

        command = ["ffmpeg", "-y"]
        for clip in clips:
            command.extend(["-i", str(clip)])
        command.extend(["-i", str(args.audio_file)])
        for overlay_path in overlay_paths:
            command.extend(["-loop", "1", "-framerate", str(args.fps), "-i", str(overlay_path)])

        filters = []
        video_labels = []
        retime_records = []
        for index, (scene, clip, probe) in enumerate(zip(scenes, clips, clip_probes)):
            source_duration = probe["duration_seconds"]
            target_duration = float(scene.get("duration_seconds") or 0)
            if target_duration <= 0:
                target_duration = (int(scene["end_us"]) - int(scene["start_us"])) / MICROSECONDS_PER_SECOND
            ratio = target_duration / source_duration
            label = f"v{index}"
            filters.append(
                f"[{index}:v]setpts={ratio:.9f}*PTS,"
                f"trim=duration={target_duration:.6f},setpts=PTS-STARTPTS,"
                f"scale={args.width}:{args.height}:force_original_aspect_ratio=decrease,"
                f"pad={args.width}:{args.height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={args.fps},format=yuv420p[{label}]"
            )
            video_labels.append(f"[{label}]")
            retime_records.append(
                {
                    "id": str(scene["id"]),
                    "clip": str(clip),
                    "source_duration_seconds": round(source_duration, 6),
                    "target_duration_seconds": round(target_duration, 6),
                    "setpts_ratio": round(ratio, 9),
                    "speed_multiplier": round(1 / ratio, 6),
                }
            )

        filters.append(
            "".join(video_labels) + f"concat=n={len(video_labels)}:v=1:a=0[vconcat]"
        )
        final_video_label = "vconcat"
        caption_renderer = "none"
        if use_libass:
            style = (
                f"FontName={args.font_name},FontSize={args.font_size},"
                "PrimaryColour=&H00F7F1DF,OutlineColour=&H00141414,"
                f"BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV={args.margin_v}"
            )
            filters.append(
                f"[vconcat]subtitles=filename='{escape_filter_path(srt_path)}':"
                f"force_style='{style}'[vout]"
            )
            final_video_label = "vout"
            caption_renderer = "ffmpeg-subtitles-libass"
        elif overlay_paths:
            previous_label = "vconcat"
            first_overlay_index = len(clips) + 1
            for index, caption in enumerate(captions):
                output_label = f"vcap{index}"
                start = int(caption["start_us"]) / MICROSECONDS_PER_SECOND
                end = int(caption["end_us"]) / MICROSECONDS_PER_SECOND
                filters.append(
                    f"[{previous_label}][{first_overlay_index + index}:v]"
                    f"overlay=x=(W-w)/2:y=H-h-{args.margin_v}:"
                    f"enable='between(t,{start:.6f},{end:.6f})':eof_action=pass"
                    f"[{output_label}]"
                )
                previous_label = output_label
            final_video_label = previous_label
            caption_renderer = "pillow-png-overlays"

        audio_index = len(clips)
        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                f"[{final_video_label}]",
                "-map",
                f"{audio_index}:a:0",
                "-c:v",
                "libx264",
                "-preset",
                args.preset,
                "-crf",
                str(args.crf),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-t",
                f"{total_duration:.6f}",
                str(args.output),
            ]
        )
        run(command)
        output_probe = probe_media(args.output)
        stream_types = [stream.get("codec_type") for stream in output_probe.get("streams", [])]
        if "video" not in stream_types or "audio" not in stream_types:
            raise RuntimeError("Preview output must contain both video and audio streams")
        if abs(output_probe["duration_seconds"] - total_duration) > 0.12:
            raise RuntimeError(
                f"Preview duration {output_probe['duration_seconds']:.3f}s differs from "
                f"plan {total_duration:.3f}s"
            )

        record_path = args.output.with_suffix(".build-record.json")
        record = {
            "timing_mode": "voice-first-dynamic",
            "timing_file": str(args.timing_file),
            "captions_file": str(args.captions_file),
            "audio_file": str(args.audio_file),
            "output": str(args.output),
            "srt_output": str(srt_path),
            "duration_seconds": round(output_probe["duration_seconds"], 6),
            "dimensions": {"width": args.width, "height": args.height},
            "captions_burned_in": not args.no_burn_captions,
            "caption_renderer": caption_renderer,
            "caption_overlay_files": [str(path) for path in overlay_paths],
            "clips": retime_records,
        }
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "duration_seconds": round(output_probe["duration_seconds"], 3),
                    "srt": str(srt_path),
                    "record": str(record_path),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr[-4000:], file=sys.stderr)
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
