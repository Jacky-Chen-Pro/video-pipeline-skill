# Workflow

## Prerequisites

- Python 3.10 or newer.
- FFmpeg and ffprobe available on `PATH`.
- Python packages installed from the bundled requirements file:

```bash
python3 -m pip install -r "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/requirements.txt"
```

- The user's own provider credentials supplied through `.env.local` or shell environment variables. Never copy credentials from the skill repository.

## 1. Plan the video

For a topic video or explainer:

- Explain one idea at a time.
- Match sentence length, tone, and visual complexity to the target audience and platform.
- Prefer concrete comparisons when they make the idea easier to understand.
- Keep a 30-75 second target unless the topic requires more.
- Verify historical dates, quantities, people, and processes against authoritative sources, then keep links and wording decisions in `fact-notes.md`.
- Confirm the visual style before keyframes or videos. Do not assume animation/cartoon; for history or serious topics, recommend documentary realism, historical illustration, cinematic reenactment, or mixed media when more appropriate.

Typical artifacts:

```text
topic
narration
style brief
storyboard
keyframe prompts
seedance scene manifest
captions
audio
video clips
jianying draft
```

Keep all artifacts for one video under one self-contained folder:

```text
videos/<project>/
  assets/
  outputs/
```

Do not place a new video's materials in top-level `assets/<project>` and `outputs/<project>` unless the user explicitly asks for the legacy layout.

## 2. Confirm visual style

Before creating final keyframes or Seedance clips, ask the user to confirm the visual style unless the user already gave one. Recommend 2-4 suitable directions and make one clear recommendation.

Common choices:

- Documentary realism for real events, people, places, products, and serious public-interest topics.
- Historical illustration for events without reliable footage, with period-appropriate clothes, architecture, props, and restrained color.
- Cinematic reenactment for narrative tension when realism is desired; avoid unsafe, graphic, or misleading depictions.
- Clean educational animation for children or abstract concepts that need simplified visuals.
- Mixed media for maps, diagrams, archival-style images, and generated scenes.

Save the confirmed decision in:

```text
videos/<project>/assets/style-brief.md
```

Use the style brief consistently in `storyboard.json`, `keyframe-prompts.md`, and `seedance_scenes.json`. If the user asks for a children's video, clean animation may be appropriate; otherwise choose the style based on topic and tone.

## 3. Generate narration and timing

Write one non-empty narration paragraph per planned scene. Generate Doubao TTS at a comfortable rate before fixing shot lengths:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/doubao_tts.py" \
  --text-file videos/<project>/assets/narration.txt \
  --output videos/<project>/outputs/audio/narration-doubao.mp3 \
  --subtitle-file videos/<project>/outputs/audio/narration-subtitles.json \
  --record-file videos/<project>/outputs/audio/generation-record.json \
  --speech-rate 0 \
  --context '语速自然，不拖慢，也不要赶，每句话有清楚停顿，符合目标受众的视频旁白语气。'
```

Build the shared timing plan and let it update the Seedance durations:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/plan_dynamic_timeline.py" \
  --narration-file videos/<project>/assets/narration.txt \
  --subtitles-file videos/<project>/outputs/audio/narration-subtitles.json \
  --scenes-file videos/<project>/assets/seedance_scenes.json \
  --audio-file videos/<project>/outputs/audio/narration-doubao.mp3 \
  --output videos/<project>/assets/timing-plan.json \
  --captions-output videos/<project>/assets/captions.json \
  --update-seedance-manifest videos/<project>/assets/seedance_scenes.json
```

Do not speed up narration to hit a fixed 30- or 60-second target. The actual narration duration becomes the video duration. If a scene exceeds Seedance's practical 12-second limit, split that scene in the copy and storyboard.

## 4. Generate keyframes

Generate final keyframes through the `imagegen` skill. This is required for production keyframes, even when the prompt does not explicitly mention `$imagegen`.

Use `imagegen`'s default built-in `image_gen` tool path unless the user explicitly requests CLI/API/model controls. Do not use local drawing scripts, SVG/HTML mockups, or non-`imagegen` image models for final keyframes. Local sketches or diagrams may be used only as planning previews, not as final Seedance source images. Only require `OPENAI_API_KEY` when the user explicitly chooses the `imagegen` CLI fallback.

Use the confirmed `style-brief.md` in every keyframe prompt. Do not write "cartoon", "children's animation", or similar style words unless the user confirmed that style.

Save final images under:

```text
videos/<project>/assets/keyframes/
```

Use 16:9 keyframes for horizontal output. Use 9:16 keyframes only when the final video is intentionally vertical and cropping is acceptable.

Avoid text inside generated images. Add captions later in Jianying.

## 5. Generate Seedance clips

Prepare `videos/<project>/assets/seedance_scenes.json` with numbered scenes. Each scene needs:

- `id`
- `title`
- `image`
- `duration`
- `prompt`

Use image paths relative to the manifest file, such as `keyframes/01-opening.png`, so the video folder can move as a unit. Run the Seedance script from the workspace root. Keep `ARK_API_KEY` in `.env.local`.

Use silent clips by default:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/seedance_image_to_video.py" \
  --manifest videos/<project>/assets/seedance_scenes.json \
  --out-dir videos/<project>/outputs/seedance \
  --state-file videos/<project>/outputs/seedance/tasks.json \
  --record-file videos/<project>/outputs/seedance/generation-record.json \
  --link-record-file videos/<project>/outputs/seedance/generation-links.private.json \
  --project <project>
```

Outputs:

- `*.mp4` clips
- `last_frames/*.png`
- `tasks.json` with scrubbed task state
- `generation-record.json` durable record
- `generation-links.private.json` temporary signed URLs

## 6. Prepare captions

Create `captions.json` as either:

```json
[
  "为什么会有白天和黑夜？",
  "地球一直在自己转动。"
]
```

or:

```json
{
  "captions": [
    {"text": "为什么会有白天和黑夜？"},
    {"text": "地球一直在自己转动。"}
  ]
}
```

For voice-first projects, prefer the timed `captions.json` written by `plan_dynamic_timeline.py`. It may contain more captions than video scenes because short spoken sentences are easier to read than one long scene caption. Each entry carries `start_us` and `end_us`, and both local preview and Jianying use those exact timestamps.

## 7. Build local preview

After Seedance clips are downloaded, lightly retime each clip to its exact scene range, mux the untouched narration, and burn captions:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/build_dynamic_preview.py" \
  --timing-file videos/<project>/assets/timing-plan.json \
  --captions-file videos/<project>/assets/captions.json \
  --clips-dir videos/<project>/outputs/seedance \
  --audio-file videos/<project>/outputs/audio/narration-doubao.mp3 \
  --output videos/<project>/outputs/local_preview/<project>-preview.mp4
```

Review the speed multiplier in the preview build record. Small differences from Seedance's integer duration are expected; split the scene if the clip would need conspicuous slow motion or acceleration.

## 8. Build Jianying draft

Refresh Seedance links first because signed URLs expire:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/seedance_image_to_video.py" \
  --manifest videos/<project>/assets/seedance_scenes.json \
  --out-dir videos/<project>/outputs/seedance \
  --state-file videos/<project>/outputs/seedance/tasks.json \
  --record-file videos/<project>/outputs/seedance/generation-record.json \
  --link-record-file videos/<project>/outputs/seedance/generation-links.private.json \
  --project <project> \
  --poll-only --timeout 120
```

Use the refreshed `outputs/seedance/generation-links.private.json` directly as the video source for Jianying. Seedance has already returned public HTTPS video URLs, so do not upload the local `outputs/seedance/*.mp4` files to another temporary host just to obtain links. Only stage assets that lack a public HTTPS URL, usually the local narration audio file.

After receiving explicit upload consent, stage the one local narration file with the default `tmpfile.link` helper. Anonymous uploads return a public direct HTTPS URL and are automatically deleted after 7 days; no anonymous early-delete method is documented. Run this immediately before creating the draft, keep the URL only in the private record, and report its expected expiration time.

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/stage_audio_tmpfile_link.py" \
  --input videos/<project>/outputs/audio/narration-doubao.mp3 \
  --record-file videos/<project>/outputs/jcaigc/staged-audio-links.private.json

AUDIO_URL=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["audio_url"])' \
  videos/<project>/outputs/jcaigc/staged-audio-links.private.json)
```

Horizontal draft at 100% source scale. Use this default for `1280x720` Seedance clips so the foreground video stays at `scale_x=1.0` and `scale_y=1.0`:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/create_jianying_draft.py" \
  --links-file videos/<project>/outputs/seedance/generation-links.private.json \
  --captions-file videos/<project>/assets/captions.json \
  --timing-file videos/<project>/assets/timing-plan.json \
  --canvas-width 1280 \
  --canvas-height 720 \
  --source-width 1280 \
  --source-height 720 \
  --foreground-scale 1.0 \
  --caption-font-size 8 \
  --caption-text-color '#f7f1df' \
  --caption-transform-y -280 \
  --audio-url "$AUDIO_URL" \
  --audio-volume 1.0 \
  --record-file videos/<project>/outputs/jcaigc/draft-record.private.json
```

Do not use `--fit-to-canvas` for ordinary horizontal drafts. On a `1920x1080` canvas with `1280x720` clips it scales foreground video to `150%`; only do that when the user explicitly asks to fill the larger canvas.

No audio:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/create_jianying_draft.py" \
  --links-file videos/<project>/outputs/seedance/generation-links.private.json \
  --captions-file videos/<project>/assets/captions.json \
  --foreground-scale 1.0 \
  --no-audio \
  --record-file videos/<project>/outputs/jcaigc/draft-record.private.json
```

## 9. Verify

Use `ffprobe` for local clips:

```bash
ffprobe -v error -print_format json -show_entries \
  format=duration:stream=codec_type,width,height \
  videos/<project>/outputs/seedance/01-*.mp4
```

Also probe narration and the final local preview. The final preview must contain both a video stream and an audio stream, and narration should end at or just before the video duration.

Confirm that scene ranges in `timing-plan.json` are contiguous and that its final `end_us` equals the measured audio duration. The Jianying draft must use this same timing file instead of a fixed `--segment-seconds` value.

Use a light HTTP check for the Jianying draft URL:

```bash
python3 - <<'PY'
import urllib.request
url = "..."
with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=30) as r:
    print(r.status, r.headers.get("Content-Type"))
PY
```
