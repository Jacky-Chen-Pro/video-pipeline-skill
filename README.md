# Video Pipeline Skill

A reusable Codex skill for producing voice-led topic videos with researched narration, confirmed visual style, generated keyframes, Doubao Seedance clips, dynamic captions, local previews, and Jianying/CapCut drafts.

## Install

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-installer/scripts/install-skill-from-github.py" \
  --repo Jacky-Chen-Pro/video-pipeline-skill \
  --path skills/video-pipeline
```

The skill becomes available as `$video-pipeline` on the next turn.

## Configure

Install the Python dependencies:

```bash
python3 -m pip install -r "${CODEX_HOME:-$HOME/.codex}/skills/video-pipeline/scripts/requirements.txt"
```

Create `.env.local` in the video workspace and provide your own credentials:

```dotenv
ARK_API_KEY=your_volcengine_ark_key
DOUBAO_SPEECH_API_KEY=your_doubao_speech_key
```

`OPENAI_API_KEY` is needed only when the user explicitly selects the imagegen CLI fallback. No API keys are included in this repository.

## External services

Depending on the requested workflow, the skill can use Volcengine Ark/Seedance, Doubao Speech, OpenAI image generation, the jcaigc/capcut-mate draft API, and tmpfile.link. The skill requires explicit user consent before uploading local media to a third-party public host.

The installable skill is located at [`skills/video-pipeline`](skills/video-pipeline).

## License

Released under the [MIT License](LICENSE).
