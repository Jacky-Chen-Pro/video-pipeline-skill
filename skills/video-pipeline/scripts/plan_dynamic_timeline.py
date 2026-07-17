#!/usr/bin/env python3
"""Build a scene timeline from Doubao TTS word timestamps."""

import argparse
import json
import math
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MICROSECONDS_PER_SECOND = 1_000_000


def normalize_text(value: str) -> str:
    return "".join(
        character
        for character in value
        if not unicodedata.category(character).startswith(("P", "Z"))
        and not character.isspace()
    )


def load_narration(path: Path) -> list[str]:
    paragraphs = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not paragraphs:
        raise ValueError(f"Narration file is empty: {path}")
    return paragraphs


def load_scene_manifest(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    scenes = data.get("scenes") if isinstance(data, dict) else data
    if not isinstance(scenes, list) or not scenes:
        raise ValueError(f"Scene manifest must contain a non-empty scene list: {path}")
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            raise ValueError(f"Scene {index} must be an object")
        scene.setdefault("id", f"{index:02d}")
        scene.setdefault("title", scene["id"])
    return scenes


def load_subtitle_events(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("subtitles") if isinstance(data, dict) else data
    if not isinstance(events, list) or not events:
        raise ValueError(f"Subtitle file must contain a non-empty event list: {path}")

    normalized_events = []
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict) or not str(event.get("text", "")).strip():
            raise ValueError(f"Subtitle event {index} has no text")
        words = event.get("words")
        if not isinstance(words, list) or not words:
            raise ValueError(f"Subtitle event {index} has no word timestamps")
        timed_words = [
            word
            for word in words
            if isinstance(word, dict)
            and isinstance(word.get("startTime"), (int, float))
            and isinstance(word.get("endTime"), (int, float))
        ]
        if not timed_words:
            raise ValueError(f"Subtitle event {index} has no usable word timestamps")
        normalized_events.append(
            {
                **event,
                "start_seconds": min(float(word["startTime"]) for word in timed_words),
                "end_seconds": max(float(word["endTime"]) for word in timed_words),
            }
        )
    return normalized_events


def probe_duration(path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    duration = float(result.stdout.strip())
    if duration <= 0:
        raise ValueError(f"Audio duration must be positive: {path}")
    return duration


def group_events_by_paragraph(
    paragraphs: list[str], events: list[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    event_index = 0

    for paragraph_index, paragraph in enumerate(paragraphs, start=1):
        target = normalize_text(paragraph)
        if not target:
            raise ValueError(f"Narration paragraph {paragraph_index} has no alignable text")

        group: list[dict[str, Any]] = []
        accumulated = ""
        while event_index < len(events) and len(accumulated) < len(target):
            event = events[event_index]
            candidate = accumulated + normalize_text(str(event["text"]))
            if not target.startswith(candidate) and candidate != target:
                raise ValueError(
                    "Subtitle alignment failed at paragraph "
                    f"{paragraph_index}: expected {target!r}, got {candidate!r}"
                )
            group.append(event)
            accumulated = candidate
            event_index += 1

        if accumulated != target:
            raise ValueError(
                f"Subtitle alignment ended early at paragraph {paragraph_index}: "
                f"expected {target!r}, got {accumulated!r}"
            )
        groups.append(group)

    if event_index != len(events):
        remaining = "".join(normalize_text(str(event["text"])) for event in events[event_index:])
        raise ValueError(f"Subtitle file has unmatched trailing text: {remaining!r}")
    return groups


def seconds_to_us(value: float) -> int:
    return int(round(value * MICROSECONDS_PER_SECOND))


def derive_scene_boundaries(
    groups: list[list[dict[str, Any]]], audio_duration: float
) -> list[tuple[float, float]]:
    spoken_ranges = [
        (group[0]["start_seconds"], group[-1]["end_seconds"])
        for group in groups
    ]
    boundaries = [0.0]
    for previous, following in zip(spoken_ranges, spoken_ranges[1:]):
        midpoint = (previous[1] + following[0]) / 2
        boundaries.append(max(boundaries[-1], midpoint))
    boundaries.append(max(audio_duration, spoken_ranges[-1][1]))
    return list(zip(boundaries, boundaries[1:]))


def generation_duration(
    target_seconds: float,
    minimum: int,
    maximum: int,
    padding: float,
) -> int:
    return min(maximum, max(minimum, math.ceil(target_seconds + padding)))


def build_captions(
    scenes: list[dict[str, Any]],
    groups: list[list[dict[str, Any]]],
    total_duration: float,
    lead_seconds: float,
    tail_seconds: float,
) -> list[dict[str, Any]]:
    captions: list[dict[str, Any]] = []
    all_events = [event for group in groups for event in group]
    next_starts = [event["start_seconds"] for event in all_events[1:]] + [total_duration]
    flat_index = 0

    for scene, group in zip(scenes, groups):
        for caption_index, event in enumerate(group, start=1):
            next_start = next_starts[flat_index]
            start = max(0.0, event["start_seconds"] - lead_seconds)
            end = min(total_duration, event["end_seconds"] + tail_seconds, next_start - 0.03)
            if end <= start:
                end = min(total_duration, max(start + 0.25, event["end_seconds"]))
            captions.append(
                {
                    "id": f"{scene['id']}-{caption_index:02d}",
                    "scene_id": str(scene["id"]),
                    "text": str(event["text"]).strip(),
                    "start": round(start, 6),
                    "end": round(end, 6),
                    "start_us": seconds_to_us(start),
                    "end_us": seconds_to_us(end),
                }
            )
            flat_index += 1
    return captions


def update_seedance_manifest(
    path: Path,
    planned_scenes: list[dict[str, Any]],
) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    scenes = data.get("scenes") if isinstance(data, dict) else data
    if not isinstance(scenes, list) or len(scenes) != len(planned_scenes):
        raise ValueError(f"Seedance scene count does not match timing plan: {path}")

    for manifest_scene, planned_scene in zip(scenes, planned_scenes):
        if str(manifest_scene.get("id")) != str(planned_scene["id"]):
            raise ValueError(
                f"Seedance scene ID {manifest_scene.get('id')} does not match "
                f"timing scene ID {planned_scene['id']}"
            )
        manifest_scene["duration"] = planned_scene["generation_duration_seconds"]
        manifest_scene["target_duration_seconds"] = planned_scene["duration_seconds"]
        manifest_scene["timeline_start_us"] = planned_scene["start_us"]
        manifest_scene["timeline_end_us"] = planned_scene["end_us"]

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Derive variable scene and caption timing from Doubao word timestamps."
    )
    parser.add_argument("--narration-file", type=Path, required=True)
    parser.add_argument("--subtitles-file", type=Path, required=True)
    parser.add_argument("--scenes-file", type=Path, required=True)
    parser.add_argument("--audio-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--captions-output", type=Path, required=True)
    parser.add_argument("--update-seedance-manifest", type=Path)
    parser.add_argument("--min-generation-seconds", type=int, default=4)
    parser.add_argument("--max-generation-seconds", type=int, default=12)
    parser.add_argument("--generation-padding-seconds", type=float, default=0.2)
    parser.add_argument("--caption-lead-seconds", type=float, default=0.08)
    parser.add_argument("--caption-tail-seconds", type=float, default=0.18)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.min_generation_seconds < 1:
            raise ValueError("--min-generation-seconds must be positive")
        if args.max_generation_seconds < args.min_generation_seconds:
            raise ValueError("--max-generation-seconds must be >= minimum")

        paragraphs = load_narration(args.narration_file)
        scenes = load_scene_manifest(args.scenes_file)
        if len(paragraphs) != len(scenes):
            raise ValueError(
                f"Narration paragraph count {len(paragraphs)} does not match "
                f"scene count {len(scenes)}"
            )

        events = load_subtitle_events(args.subtitles_file)
        groups = group_events_by_paragraph(paragraphs, events)
        audio_duration = probe_duration(args.audio_file)
        ranges = derive_scene_boundaries(groups, audio_duration)

        planned_scenes = []
        warnings = []
        for scene, paragraph, group, (start, end) in zip(scenes, paragraphs, groups, ranges):
            duration = end - start
            generated_seconds = generation_duration(
                duration,
                args.min_generation_seconds,
                args.max_generation_seconds,
                args.generation_padding_seconds,
            )
            playback_ratio = generated_seconds / duration
            if duration > args.max_generation_seconds:
                warnings.append(
                    f"Scene {scene['id']} is {duration:.3f}s, longer than the "
                    f"{args.max_generation_seconds}s generation limit; split the scene "
                    "if the required slowdown is visually distracting."
                )
            planned_scenes.append(
                {
                    "id": str(scene["id"]),
                    "title": str(scene.get("title") or scene["id"]),
                    "narration": paragraph,
                    "start": round(start, 6),
                    "end": round(end, 6),
                    "start_us": seconds_to_us(start),
                    "end_us": seconds_to_us(end),
                    "duration_seconds": round(duration, 6),
                    "duration_us": seconds_to_us(duration),
                    "spoken_start": round(group[0]["start_seconds"], 6),
                    "spoken_end": round(group[-1]["end_seconds"], 6),
                    "spoken_start_us": seconds_to_us(group[0]["start_seconds"]),
                    "spoken_end_us": seconds_to_us(group[-1]["end_seconds"]),
                    "generation_duration_seconds": generated_seconds,
                    "generated_to_timeline_ratio": round(playback_ratio, 6),
                    "subtitle_event_count": len(group),
                }
            )

        total_duration_us = seconds_to_us(audio_duration)
        captions = build_captions(
            scenes,
            groups,
            audio_duration,
            args.caption_lead_seconds,
            args.caption_tail_seconds,
        )
        plan = {
            "schema_version": 1,
            "timing_mode": "voice-first-dynamic",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "narration_file": str(args.narration_file),
                "subtitles_file": str(args.subtitles_file),
                "audio_file": str(args.audio_file),
                "scenes_file": str(args.scenes_file),
            },
            "audio_duration_seconds": round(audio_duration, 6),
            "audio_duration_us": total_duration_us,
            "total_duration_seconds": round(audio_duration, 6),
            "total_duration_us": total_duration_us,
            "generation_limits_seconds": {
                "minimum": args.min_generation_seconds,
                "maximum": args.max_generation_seconds,
                "padding": args.generation_padding_seconds,
            },
            "warnings": warnings,
            "scenes": planned_scenes,
        }
        caption_document = {
            "schema_version": 1,
            "timing_mode": "word-timestamp-derived",
            "total_duration_us": total_duration_us,
            "captions": captions,
        }

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.captions_output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        args.captions_output.write_text(
            json.dumps(caption_document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if args.update_seedance_manifest:
            update_seedance_manifest(args.update_seedance_manifest, planned_scenes)

        print(
            json.dumps(
                {
                    "timing_plan": str(args.output),
                    "captions": str(args.captions_output),
                    "duration_seconds": round(audio_duration, 3),
                    "scene_durations": [
                        round(scene["duration_seconds"], 3) for scene in planned_scenes
                    ],
                    "generation_durations": [
                        scene["generation_duration_seconds"] for scene in planned_scenes
                    ],
                    "warnings": warnings,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
