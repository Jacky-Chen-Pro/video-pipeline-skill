# Formats And API Notes

## Seedance scene manifest

```json
[
  {
    "id": "01",
    "title": "opening_question",
    "image": "keyframes/01-opening.png",
    "duration": 8,
    "target_duration_seconds": 7.462,
    "prompt": "使用已确认的视频视觉风格，画面轻轻推进，不出现文字、字幕、标志或水印。"
  }
]
```

Optional fields:

- `resolution`: default script argument is `720p`
- `ratio`: default script argument is `16:9`
- `seed`: if reproducibility is desired
- `target_duration_seconds`: exact voice-led scene duration for records; Seedance still receives integer `duration`

Prompt style rules:

- Base every prompt on `assets/style-brief.md`.
- Do not default to animation, cartoon, or children's styles unless the user confirmed that direction.
- For historical topics, prefer period-appropriate documentary realism, historical illustration, cinematic reenactment, or mixed media when they fit the user's chosen tone.
- Avoid generated text inside images; add captions later.

Seedance 1.5 Pro notes:

- Model: `doubao-seedance-1-5-pro-251215`
- Endpoint: `https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks`
- Auth: `Authorization: Bearer $ARK_API_KEY`
- Supports first-frame image-to-video and first/last-frame image-to-video.
- `duration` range for 1.5 Pro is 4-12 seconds, or `-1`.
- `frames` is not supported for 1.5 Pro.
- Use `generate_audio: false` when audio will be added later.
- Output video and last-frame URLs are signed temporary URLs, usually around 24 hours.

## Generation records

Durable record:

```text
videos/<project>/outputs/seedance/generation-record.json
```

Private temporary links:

```text
videos/<project>/outputs/seedance/generation-links.private.json
```

The private file contains signed `video_url` and `last_frame_url` values. Keep it out of git.

## Doubao TTS 2.0 bidirectional WebSocket

- Endpoint: `wss://openspeech.bytedance.com/api/v3/tts/bidirection`
- Authentication header: `X-Api-Key: $DOUBAO_SPEECH_API_KEY`
- Resource header for built-in 2.0 voices: `X-Api-Resource-Id: seed-tts-2.0`
- Default narrator: `zh_female_cancan_uranus_bigtts` (`知性灿灿 2.0`)
- Connection flow: `StartConnection`, `StartSession`, one or more `TaskRequest` events, `FinishSession`, then drain responses through `SessionFinished`, followed by `FinishConnection`.
- Include `event` and `namespace: BidirectionalTTS` inside StartSession and TaskRequest JSON payloads.
- Task text belongs at `req_params.text`. Repeat `speaker` and `audio_params` in each TaskRequest.
- `additions` is a JSON-encoded string. Put `explicit_language` and `context_texts` inside that string rather than directly under `req_params`.
- For reliable word timing events, set both `audio_params.enable_subtitle` and `audio_params.enable_timestamp` to `true`.
- Streamed MP3 frames can be concatenated in receive order. Verify the completed file with `ffprobe` before muxing it into video.

## Jianying timeline units

Jianying/capcut-mate timeline values are microseconds.

Examples:

```text
5 seconds = 5_000_000
30 seconds = 30_000_000
```

For a voice-first project, scene ranges can differ:

```json
[
  {"start": 0, "end": 7462000},
  {"start": 7462000, "end": 16218000},
  {"start": 16218000, "end": 23891000}
]
```

`timing-plan.json` is the source of truth. Scene ranges must be contiguous, start at zero, and finish at the measured narration duration. Timed captions use their own `start_us` and `end_us` values and do not need to match the scene count.

## Jianying remote media lifecycle

- The hosted `add_videos` and `add_audios` APIs fetch media from public HTTPS URLs; local filesystem paths are not accepted.
- Seedance output videos already have public HTTPS URLs in `outputs/seedance/generation-links.private.json`; pass that file directly to `create_jianying_draft.py --links-file`. Do not stage or re-upload local Seedance `.mp4` files unless the Seedance URLs cannot be refreshed or fetched.
- Verify remote media with an HTTP range request before creating the draft. Some signed Seedance URLs reject `HEAD` even when `GET` works.
- Signed Seedance URLs may retain their original expiry after a poll-only refresh. Parse the signature timestamp and expiry rather than assuming a fresh 24-hour window.
- Stage only local media that lacks a public HTTPS URL, usually narration audio. Save staged audio URLs in a private record such as `staged-audio-links.private.json`, and tell the user the latest recommended import time based on both the Seedance video links and staged audio link.
- Pass `--timing-file` so video scenes and captions use the same dynamic ranges. The timing plan supplies narration duration; use `--audio-duration-us` only as an explicit override.

## Default narration staging: tmpfile.link

- Endpoint: `POST https://tmpfile.link/api/upload` as `multipart/form-data`; required part: `file`.
- Anonymous uploads return `fileName`, `downloadLink`, `downloadLinkEncoded`, `size`, `type`, and `uploadedTo: "public"`. Use `downloadLinkEncoded` for `add_audios` only after verifying it with a GET range request.
- The direct `d.tmpfile.link` URL is public to anyone holding it. Anonymous files are automatically deleted after 7 days; no anonymous early-delete method is documented.
- Keep the URL and calculated expiry only in `outputs/jcaigc/staged-audio-links.private.json`. Stage just before draft creation; never re-upload Seedance video files.

## Jianying layout decisions

If source videos are 16:9:

- Horizontal draft matching source resolution, such as `1280x720`: default choice for Seedance `720p` clips; keeps foreground scale at `100%`.
- Horizontal draft `1920x1080` with foreground scale `1.0`: keeps original video scale but may leave empty canvas around a `1280x720` clip depending on the editor layout.
- Horizontal draft `1920x1080` with `--fit-to-canvas`: fills canvas by scaling `1280x720` clips to `150%`; do this only when the user explicitly asks to fill the larger canvas.
- Vertical draft `1080x1920` with fit-width: preserves all content but leaves empty top/bottom or black areas.
- Vertical draft with cover: fills canvas but crops left/right.
- Vertical draft with background layer: hides black areas but adds visual complexity.

Do not upscale horizontal foreground videos above `1.0` or use background layers unless requested.

## Captions

Known working explainer caption style:

```text
font: 江湖体
font_size: 8 for 1920x1080, 9 for 1080x1920
text_color: #f7f1df
border_color: #000000
line_spacing: 10
transform_y: about -430 for 1920x1080
```

If the user gives exact subtitle parameters, follow them first. If they complain about readability, reduce font size and use warm white or soft cream instead of saturated yellow.

## Background music

The jcaigc/capcut-mate API supports adding audio by URL via `add_audios`.

It does not expose a documented endpoint for fetching Jianying/CapCut's internal trending music library. If the user requests internal popular music, explain this limitation and ask for or find a usable audio URL. Keep BGM around `0.25-0.40` volume when narration or educational captions matter.
