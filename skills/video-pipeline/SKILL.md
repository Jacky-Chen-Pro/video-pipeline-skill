---
name: video-pipeline
description: End-to-end workflow for producing topic videos and explainers into a single self-contained video folder with researched copy, confirmed visual style, storyboard, keyframes, Seedance clips, generation records, dynamic captions, natural-speed narration, local previews, and Jianying/CapCut draft links. Use when the user asks to make or automate a video from a topic, create keyframe-to-video batches with doubao-seedance, preserve prompts/video URLs, add voiceover and captions, or assemble voice-led clips into Jianying drafts.
---

# 视频流水线

## Core Workflow

Use this skill to turn a topic into a reusable video production pipeline. Keep replies in Chinese when the user is Chinese.

1. Research factual claims when needed, then draft narration for the user's target audience and platform, with one paragraph per planned scene. For history, prefer museums, archives, and government cultural institutions, and record sources in `fact-notes.md`.
2. Confirm the visual style before generating keyframes or videos. Do not assume animation/cartoon merely because a previous project used it. If the user has not explicitly chosen a style, recommend the best 2-4 options for the topic and wait for confirmation. Record the confirmed choice in `style-brief.md`.
3. Generate narration at a natural, comfortable rate and keep Doubao word timestamps. Do not accelerate the voice merely to fit a preselected video duration.
4. Run `scripts/plan_dynamic_timeline.py`. Treat its `timing-plan.json` as the timing source of truth for scene lengths, captions, previews, and Jianying drafts.
5. Design 4-8 storyboard shots and keyframes around the measured scene lengths and the confirmed visual style. Plan transitions between adjacent clips so each clip's ending leads naturally into the next clip's opening. Prefer concrete continuity devices such as shared objects, consistent camera direction, matched character positions, action cues, first/last-frame references, or prompts that explicitly describe how the previous scene settles and how the next scene begins. Avoid abrupt topic, scale, color, or camera changes unless the story intentionally calls for a clear cut.
6. Generate final keyframe images through the `imagegen` skill. Use `imagegen`'s default built-in `image_gen` tool path unless the user explicitly requests CLI/API/model controls. Do not use local drawing scripts, SVG/HTML mockups, or non-`imagegen` image models for final keyframes. Only require `OPENAI_API_KEY` when the user explicitly chooses the `imagegen` CLI fallback. Save final images into the video's project folder, not only the generated-images cache.
7. Create a Seedance scene manifest with one item per keyframe. Copy the planner's per-scene `generation_duration_seconds` into each scene's `duration`; values may differ by scene.
8. Run `scripts/seedance_image_to_video.py` to generate video clips, save mp4 files, tail frames, task IDs, prompts, seeds, usage, and temporary links.
9. Run `scripts/build_dynamic_preview.py` to retime each generated clip lightly to the exact voice-led scene duration, burn timestamp-derived captions, and mux the original narration without changing its speed.
10. Refresh temporary Seedance links immediately before building a Jianying draft. Use `outputs/seedance/generation-links.private.json` directly as the Jianying video source; do not upload or re-stage generated Seedance `.mp4` files just to get video URLs.
11. Stage only media that does not already have a public HTTPS URL, usually local narration audio. By default use `scripts/stage_audio_tmpfile_link.py` to upload one narration file to `tmpfile.link`, then run `scripts/create_jianying_draft.py --timing-file ...` with Seedance links for video and the staged audio URL for narration. Save the draft link and the private staging record.
12. Keep durable records and private temporary-link records.

Read `references/workflow.md` when executing the full pipeline. Read `references/formats-and-api-notes.md` when preparing manifests, captions, or troubleshooting API parameters.

## Project Layout

Use one self-contained folder per video. Do not scatter a video's copy, images, clips, records, and drafts across top-level `assets/` and `outputs/` folders.

```text
videos/<project>/
  assets/
    keyframes/
    seedance_scenes.json
    captions.json
    storyboard.json
    narration.md
    narration.txt
    style-brief.md
    keyframe-prompts.md
    fact-notes.md
    timing-plan.json
  outputs/
    audio/
      narration-doubao.mp3
      narration-subtitles.json
      generation-record.json
    seedance/
      last_frames/
      tasks.json
      generation-record.json
      generation-links.private.json
    jcaigc/
      draft-record.private.json
      staged-media-links.private.json
      staged-audio-links.private.json
      final-draft-url.txt
    local_preview/
```

Use stable, numbered filenames such as `01-opening.png`, `02-explain-rotation.png`, and matching scene IDs `01`, `02`, etc. In `seedance_scenes.json`, prefer image paths relative to the manifest file, such as `keyframes/01-opening.png`.

## Secrets

Use `.env.local` or shell environment variables. Never ask the user to paste API keys into chat.

```bash
ARK_API_KEY=...
DOUBAO_SPEECH_API_KEY=...
```

Do not print API keys. Put `.env`, `.env.*`, and `*.private.json` in `.gitignore`.

## External Upload Consent

Before uploading any local image, audio, video, script, or project file to a third-party public host, pause and obtain the user's explicit confirmation. State the provider, exact files and count, whether links are public or unlisted, retention period, early-deletion capability, and why the upload is needed. Do not treat approval to create a Jianying draft as approval to upload files to another provider. Prefer local-only handling or storage owned by the user when available, and never upload before confirmation.

For Jianying drafts, Seedance output videos already have temporary HTTPS URLs in `outputs/seedance/generation-links.private.json`. Do not ask to upload those generated video files or stage them elsewhere unless the Seedance links cannot be refreshed or cannot be fetched by the draft API. Usually only the local narration file, such as `outputs/audio/narration-doubao.mp3`, needs staging.

The default audio staging provider is `tmpfile.link`. Anonymous uploads return a public `d.tmpfile.link` HTTPS URL that anyone holding the link can fetch. The provider automatically deletes anonymous files after 7 days and documents no anonymous early-deletion method, so do not promise early deletion. Save its URL only in `outputs/jcaigc/staged-audio-links.private.json`; create the Jianying draft immediately after staging and state the exact expiry time to the user.

## Visual Style Confirmation

Before creating final keyframes or Seedance videos, confirm the video's visual style with the user unless they already gave an explicit style. Recommend the style that best fits the topic and audience; do not default to cartoon/animation.

Typical options:

- Documentary realism: suitable for history, biography, places, real products, and serious public-interest topics.
- Historical illustration: suitable for events without reliable live footage, using period-appropriate clothing, architecture, props, and restrained color.
- Cinematic reenactment: suitable for dramatic stories when realism is desired but no real footage exists; avoid unsafe or graphic depictions.
- Clean educational animation: suitable when the target audience is children or when abstract concepts need simple visual explanation.
- Mixed media: suitable when combining maps, archival-style visuals, diagrams, and generated scenes.

When asking for confirmation, give a short recommendation such as: "For this topic I recommend historical illustration with documentary realism, because it feels serious without pretending to be real footage. I can also do clean educational animation if the target audience is young children." After confirmation, save the choice and any constraints in `assets/style-brief.md` and reflect it consistently in storyboard, keyframe prompts, captions, and Seedance prompts.

## Seedance Generation

Create `videos/<project>/assets/seedance_scenes.json`, then run:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/seedance_image_to_video.py" \
  --manifest videos/<project>/assets/seedance_scenes.json \
  --out-dir videos/<project>/outputs/seedance \
  --state-file videos/<project>/outputs/seedance/tasks.json \
  --record-file videos/<project>/outputs/seedance/generation-record.json \
  --link-record-file videos/<project>/outputs/seedance/generation-links.private.json \
  --project <project>
```

Default model: `doubao-seedance-1-5-pro-251215`. Default output is silent (`generate_audio: false`) unless `--generate-audio` is passed.

## Doubao Narration

For a single Chinese narrator, use the V3 bidirectional WebSocket script. The default voice is `知性灿灿 2.0` (`zh_female_cancan_uranus_bigtts`) with resource ID `seed-tts-2.0`. Keep generated audio and records inside the video's own `outputs/audio/` folder.

The script requires the Python `websockets` package. It streams every narration paragraph as one text request, then joins the returned audio frames into a durable local file.

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/doubao_tts.py" \
  --text-file videos/<project>/assets/narration.txt \
  --output videos/<project>/outputs/audio/narration-doubao.mp3 \
  --subtitle-file videos/<project>/outputs/audio/narration-subtitles.json \
  --record-file videos/<project>/outputs/audio/generation-record.json \
  --context '请用清晰、自然、符合目标受众的视频旁白语气朗读。'
```

Use one `speaker` for ordinary narration. Dialogue is a separate workflow that synthesizes each role with a different speaker and then assembles the clips on a timeline.

Use `--speech-rate 0` as the ordinary starting point. A small adjustment is acceptable after listening, but do not post-process narration speed to force a fixed total duration. Give the TTS clear punctuation and a context such as “语速自然，不拖慢，也不要赶，每句话有清楚停顿”。

## Dynamic Timing

Generate narration before deciding final clip lengths. Keep exactly one non-empty narration paragraph per storyboard scene, then derive scene boundaries and timed captions from the returned word timestamps:

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

The planner divides pauses between adjacent scenes, keeps the complete audio duration, and selects an integer Seedance generation duration from 4-12 seconds for each scene. Split narration or add a scene when a paragraph is materially longer than the provider's 12-second limit; do not solve a large mismatch with obvious slow motion.

After clips are downloaded, create the local synchronized preview:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/build_dynamic_preview.py" \
  --timing-file videos/<project>/assets/timing-plan.json \
  --captions-file videos/<project>/assets/captions.json \
  --clips-dir videos/<project>/outputs/seedance \
  --audio-file videos/<project>/outputs/audio/narration-doubao.mp3 \
  --output videos/<project>/outputs/local_preview/<project>-preview.mp4
```

The preview builder uses FFmpeg's `subtitles` filter when libass is available. When it is not, it automatically renders timed Chinese caption PNG overlays with Pillow and a local CJK font, so no system FFmpeg reinstall is required.

Before using links in another API, refresh them:

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

## Jianying Drafts

For horizontal 16:9 clips, keep foreground video scale at 100% by default. Do not upscale `1280x720` Seedance clips to `150%` just to fill a `1920x1080` canvas. Prefer a canvas that matches the source clip resolution (usually `1280x720`) when the user wants a clean horizontal draft without scaling. Only use `--fit-to-canvas` or a scale above `1.0` when the user explicitly asks to fill a larger canvas.

Use `outputs/seedance/generation-links.private.json` directly for `--links-file`. These Seedance video URLs are already the public HTTPS inputs required by `add_videos`; do not upload the local `outputs/seedance/*.mp4` copies to a temporary host merely to create another video URL. Refresh and HTTP-range verify the Seedance links before drafting. If narration is local, stage only the audio file and pass that staged URL to `--audio-url`.

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
  --audio-url '<audio-url>' \
  --audio-volume 1.0 \
  --record-file videos/<project>/outputs/jcaigc/draft-record.private.json
```

`add_audios` requires a publicly reachable HTTPS URL. With `--timing-file`, narration duration comes from the timing plan; `--audio-duration-us` remains available as an explicit override. Ensure every staged audio URL remains valid long enough for the user to import the draft. Keep staged audio URLs in a private record file and follow External Upload Consent before staging any local file.

Stage the local narration immediately before creating the draft. The default `tmpfile.link` staging flow uploads only one audio file and records its public URL privately:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/stage_audio_tmpfile_link.py" \
  --input videos/<project>/outputs/audio/narration-doubao.mp3 \
  --record-file videos/<project>/outputs/jcaigc/staged-audio-links.private.json

AUDIO_URL=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["audio_url"])' \
  videos/<project>/outputs/jcaigc/staged-audio-links.private.json)
```

The script uses the provider's documented `curl` multipart request, validates the source size and anonymous-public response, verifies the direct HTTPS URL with an HTTP range request, and records the expected 7-day expiry. Do not log or commit the URL.

For vertical drafts with horizontal footage, explain the tradeoff:

- Fit full video: black/empty areas appear.
- Cover full canvas: crops left/right.
- Add blurred or enlarged background: no black bars, but visually busier.

Only use the background-layer option (`--optimized-layout`) when the user explicitly wants a vertical draft without pure black empty areas.

## Validation

After each run:

- Use `ffprobe` to confirm clip duration, dimensions, and whether audio streams exist.
- Use `ffprobe` to confirm narration codec, sample rate, channels, and duration before mixing it into a preview or draft.
- Verify `timing-plan.json` starts at zero, has contiguous scene ranges, ends at the measured narration duration, and contains no unnecessarily large clip speed change.
- Verify the local preview duration matches the timing plan, contains video and audio streams, and keeps the original narration speed.
- Open or inspect a contact sheet if visual quality matters.
- Verify `generation-record.json` contains prompts, parameters, task IDs, seeds, usage, and local paths.
- Verify `generation-links.private.json` contains temporary URLs only when needed and remains ignored by git.
- Verify the final Jianying draft URL returns HTTP 200.
