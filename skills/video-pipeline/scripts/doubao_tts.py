#!/usr/bin/env python3
"""Generate narration with Doubao Speech's bidirectional WebSocket TTS API."""

import argparse
import asyncio
import gzip
import json
import os
import re
import struct
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import websockets
from websockets.exceptions import WebSocketException


DEFAULT_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
DEFAULT_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_SPEAKER = "zh_female_cancan_uranus_bigtts"
DEFAULT_SPEAKER_NAME = "知性灿灿 2.0"

MSG_FULL_CLIENT_REQUEST = 0b0001
MSG_AUDIO_ONLY_SERVER = 0b1011
MSG_ERROR = 0b1111

FLAG_NO_SEQUENCE = 0b0000
FLAG_POSITIVE_SEQUENCE = 0b0001
FLAG_NEGATIVE_SEQUENCE = 0b0011
FLAG_WITH_EVENT = 0b0100

SERIALIZATION_JSON = 0b0001
COMPRESSION_NONE = 0b0000
COMPRESSION_GZIP = 0b0001

EVENT_START_CONNECTION = 1
EVENT_FINISH_CONNECTION = 2
EVENT_CONNECTION_STARTED = 50
EVENT_CONNECTION_FAILED = 51
EVENT_CONNECTION_FINISHED = 52
EVENT_START_SESSION = 100
EVENT_CANCEL_SESSION = 101
EVENT_FINISH_SESSION = 102
EVENT_SESSION_STARTED = 150
EVENT_SESSION_CANCELED = 151
EVENT_SESSION_FINISHED = 152
EVENT_SESSION_FAILED = 153
EVENT_USAGE_RESPONSE = 154
EVENT_TASK_REQUEST = 200
EVENT_TTS_SENTENCE_START = 350
EVENT_TTS_SENTENCE_END = 351
EVENT_TTS_RESPONSE = 352
EVENT_TTS_ENDED = 359
EVENT_TTS_SUBTITLE = 364

CONNECTION_EVENTS_WITHOUT_SESSION_ID = {
    EVENT_START_CONNECTION,
    EVENT_FINISH_CONNECTION,
    EVENT_CONNECTION_STARTED,
    EVENT_CONNECTION_FAILED,
    EVENT_CONNECTION_FINISHED,
}
CONNECTION_RESPONSE_EVENTS = {
    EVENT_CONNECTION_STARTED,
    EVENT_CONNECTION_FAILED,
    EVENT_CONNECTION_FINISHED,
}
FAILURE_EVENTS = {EVENT_CONNECTION_FAILED, EVENT_SESSION_FAILED}
EVENT_NAMES = {
    EVENT_START_CONNECTION: "StartConnection",
    EVENT_FINISH_CONNECTION: "FinishConnection",
    EVENT_CONNECTION_STARTED: "ConnectionStarted",
    EVENT_CONNECTION_FAILED: "ConnectionFailed",
    EVENT_CONNECTION_FINISHED: "ConnectionFinished",
    EVENT_START_SESSION: "StartSession",
    EVENT_CANCEL_SESSION: "CancelSession",
    EVENT_FINISH_SESSION: "FinishSession",
    EVENT_SESSION_STARTED: "SessionStarted",
    EVENT_SESSION_CANCELED: "SessionCanceled",
    EVENT_SESSION_FINISHED: "SessionFinished",
    EVENT_SESSION_FAILED: "SessionFailed",
    EVENT_USAGE_RESPONSE: "UsageResponse",
    EVENT_TASK_REQUEST: "TaskRequest",
    EVENT_TTS_SENTENCE_START: "TTSSentenceStart",
    EVENT_TTS_SENTENCE_END: "TTSSentenceEnd",
    EVENT_TTS_RESPONSE: "TTSResponse",
    EVENT_TTS_ENDED: "TTSEnded",
    EVENT_TTS_SUBTITLE: "TTSSubtitle",
}


@dataclass
class Message:
    message_type: int
    flag: int
    serialization: int
    compression: int
    event: int = 0
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0
    payload: bytes = b""


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


def json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def pack_sized_bytes(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


def encode_event(event: int, payload: bytes, session_id: str = "") -> bytes:
    header = bytes(
        [
            (1 << 4) | 1,
            (MSG_FULL_CLIENT_REQUEST << 4) | FLAG_WITH_EVENT,
            (SERIALIZATION_JSON << 4) | COMPRESSION_NONE,
            0,
        ]
    )
    parts = [header, struct.pack(">i", event)]
    if event not in CONNECTION_EVENTS_WITHOUT_SESSION_ID:
        parts.append(pack_sized_bytes(session_id.encode("utf-8")))
    parts.append(pack_sized_bytes(payload))
    return b"".join(parts)


def read_sized_bytes(data: bytes, offset: int, label: str) -> tuple[bytes, int]:
    if offset + 4 > len(data):
        raise ValueError(f"Missing {label} length")
    size = struct.unpack_from(">I", data, offset)[0]
    offset += 4
    end = offset + size
    if end > len(data):
        raise ValueError(f"Incomplete {label}: expected {size} bytes")
    return data[offset:end], end


def decode_message(data: bytes) -> Message:
    if len(data) < 4:
        raise ValueError(f"WebSocket message is too short: {len(data)} bytes")

    version = data[0] >> 4
    header_words = data[0] & 0x0F
    if version != 1 or header_words < 1:
        raise ValueError(f"Unsupported protocol header: version={version}, words={header_words}")

    header_size = header_words * 4
    if len(data) < header_size:
        raise ValueError("Incomplete protocol header")

    message_type = data[1] >> 4
    flag = data[1] & 0x0F
    serialization = data[2] >> 4
    compression = data[2] & 0x0F
    offset = header_size
    message = Message(message_type, flag, serialization, compression)

    if message_type != MSG_ERROR and flag in (FLAG_POSITIVE_SEQUENCE, FLAG_NEGATIVE_SEQUENCE):
        if offset + 4 > len(data):
            raise ValueError("Missing sequence number")
        message.sequence = struct.unpack_from(">i", data, offset)[0]
        offset += 4
    elif message_type == MSG_ERROR:
        if offset + 4 > len(data):
            raise ValueError("Missing error code")
        message.error_code = struct.unpack_from(">I", data, offset)[0]
        offset += 4

    if flag == FLAG_WITH_EVENT:
        if offset + 4 > len(data):
            raise ValueError("Missing event number")
        message.event = struct.unpack_from(">i", data, offset)[0]
        offset += 4

        if message.event not in CONNECTION_EVENTS_WITHOUT_SESSION_ID:
            session_bytes, offset = read_sized_bytes(data, offset, "session ID")
            message.session_id = session_bytes.decode("utf-8")

        if message.event in CONNECTION_RESPONSE_EVENTS:
            connect_bytes, offset = read_sized_bytes(data, offset, "connect ID")
            message.connect_id = connect_bytes.decode("utf-8")

    if offset + 4 > len(data):
        raise ValueError("Missing payload length")
    declared_payload_size = struct.unpack_from(">I", data, offset)[0]
    offset += 4
    available_payload_size = len(data) - offset
    if declared_payload_size > available_payload_size:
        raise ValueError(
            f"Incomplete payload: expected {declared_payload_size} bytes, "
            f"got {available_payload_size}"
        )
    # Some sentence metadata responses report Unicode character count instead
    # of UTF-8 byte count. A WebSocket frame contains exactly one payload, so
    # consuming the remaining frame bytes is safe and preserves Chinese text.
    message.payload = data[offset:]
    if compression == COMPRESSION_GZIP and message.payload:
        message.payload = gzip.decompress(message.payload)
    elif compression not in (COMPRESSION_NONE, COMPRESSION_GZIP):
        raise ValueError(f"Unsupported payload compression: {compression}")
    return message


def payload_json(message: Message) -> Optional[Any]:
    if not message.payload:
        return None
    try:
        return json.loads(message.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def event_name(event: int) -> str:
    return EVENT_NAMES.get(event, f"Event({event})")


def describe_failure(message: Message) -> str:
    decoded = payload_json(message)
    if decoded is not None:
        detail = json.dumps(decoded, ensure_ascii=False)
    else:
        detail = message.payload.decode("utf-8", errors="replace")
    detail = detail[:1000]
    if message.message_type == MSG_ERROR:
        return f"Doubao protocol error {message.error_code}: {detail}"
    return f"Doubao {event_name(message.event)}: {detail}"


async def receive_message(websocket: Any, timeout: float, verbose: bool) -> Message:
    raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
    if not isinstance(raw, bytes):
        raise ValueError("Doubao returned an unexpected text WebSocket message")
    message = decode_message(raw)
    if verbose:
        print(
            f"received {event_name(message.event)} type={message.message_type} "
            f"bytes={len(message.payload)}",
            file=sys.stderr,
        )
    if message.message_type == MSG_ERROR or message.event in FAILURE_EVENTS:
        raise RuntimeError(describe_failure(message))
    return message


async def wait_for_event(websocket: Any, expected: int, timeout: float, verbose: bool) -> Message:
    while True:
        message = await receive_message(websocket, timeout, verbose)
        if message.event == expected:
            return message


def split_text(text: str, max_chars: int) -> list[str]:
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    if not paragraphs:
        return []

    segments: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            segments.append(paragraph)
            continue

        sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;])", paragraph) if item.strip()]
        current = ""
        for sentence in sentences:
            if current and len(current) + len(sentence) > max_chars:
                segments.append(current)
                current = ""
            while len(sentence) > max_chars:
                if current:
                    segments.append(current)
                    current = ""
                segments.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            current += sentence
        if current:
            segments.append(current)
    return segments


def merge_usage(current: dict[str, Any], candidate: Optional[Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return current
    usage = candidate.get("usage")
    if isinstance(usage, dict):
        return {**current, **usage}
    return {**current, **candidate}


def capture_response(
    message: Message,
    audio_chunks: list[bytes],
    subtitles: list[Any],
    usage: dict[str, Any],
    event_counts: dict[str, int],
) -> dict[str, Any]:
    name = event_name(message.event)
    event_counts[name] = event_counts.get(name, 0) + 1

    if message.message_type == MSG_AUDIO_ONLY_SERVER and message.payload:
        audio_chunks.append(message.payload)
    elif (
        message.event == EVENT_TTS_RESPONSE
        and message.serialization == 0
        and message.payload
    ):
        audio_chunks.append(message.payload)
    elif message.event == EVENT_TTS_SUBTITLE:
        decoded = payload_json(message)
        subtitles.append(decoded if decoded is not None else {})
    elif message.event in (EVENT_USAGE_RESPONSE, EVENT_SESSION_FINISHED):
        usage = merge_usage(usage, payload_json(message))
    return usage


async def synthesize(
    args: argparse.Namespace,
    api_key: str,
    segments: list[str],
) -> dict[str, Any]:
    connect_request_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": args.resource_id,
        "X-Api-Connect-Id": connect_request_id,
        "X-Control-Require-Usage-Tokens-Return": "*",
    }

    audio_params: dict[str, Any] = {
        "format": args.format,
        "sample_rate": args.sample_rate,
        "speech_rate": args.speech_rate,
        "loudness_rate": args.loudness_rate,
        "enable_subtitle": not args.no_subtitles,
    }
    if not args.no_subtitles:
        audio_params["enable_timestamp"] = True
    if args.format == "mp3":
        audio_params["bit_rate"] = args.bit_rate

    request_params: dict[str, Any] = {
        "speaker": args.speaker,
        "audio_params": audio_params,
    }
    if args.model:
        request_params["model"] = args.model
    additions: dict[str, Any] = {}
    if args.explicit_language:
        additions["explicit_language"] = args.explicit_language
    if args.context:
        additions["context_texts"] = args.context
    if additions:
        request_params["additions"] = json.dumps(
            additions, ensure_ascii=False, separators=(",", ":")
        )

    audio_chunks: list[bytes] = []
    subtitles: list[Any] = []
    usage: dict[str, Any] = {}
    event_counts: dict[str, int] = {}
    connect_id = ""

    async with websockets.connect(
        args.endpoint,
        extra_headers=headers,
        compression=None,
        max_size=None,
        open_timeout=args.timeout,
        close_timeout=args.timeout,
        ping_interval=20,
        ping_timeout=20,
    ) as websocket:
        await websocket.send(encode_event(EVENT_START_CONNECTION, b"{}"))
        connection = await wait_for_event(
            websocket, EVENT_CONNECTION_STARTED, args.timeout, args.verbose
        )
        connect_id = connection.connect_id

        await websocket.send(
            encode_event(
                EVENT_START_SESSION,
                json_bytes(
                    {
                        "event": EVENT_START_SESSION,
                        "namespace": "BidirectionalTTS",
                        "req_params": request_params,
                    }
                ),
                session_id,
            )
        )
        await wait_for_event(websocket, EVENT_SESSION_STARTED, args.timeout, args.verbose)

        for segment in segments:
            task_params: dict[str, Any] = {
                "text": segment,
                "speaker": args.speaker,
                "audio_params": audio_params,
            }
            await websocket.send(
                encode_event(
                    EVENT_TASK_REQUEST,
                    json_bytes(
                        {
                            "event": EVENT_TASK_REQUEST,
                            "namespace": "BidirectionalTTS",
                            "req_params": task_params,
                        }
                    ),
                    session_id,
                )
            )
            if args.request_interval_ms:
                await asyncio.sleep(args.request_interval_ms / 1000)

        # FinishSession marks the end of the streamed text input. The service
        # may hold sentence-end events until this marker arrives.
        await websocket.send(encode_event(EVENT_FINISH_SESSION, b"{}", session_id))

        while True:
            message = await receive_message(websocket, args.timeout, args.verbose)
            usage = capture_response(
                message, audio_chunks, subtitles, usage, event_counts
            )
            if message.event == EVENT_SESSION_FINISHED:
                break

        await websocket.send(encode_event(EVENT_FINISH_CONNECTION, b"{}"))
        await wait_for_event(websocket, EVENT_CONNECTION_FINISHED, args.timeout, args.verbose)

    audio = b"".join(audio_chunks)
    if not audio:
        raise RuntimeError("Doubao session finished without returning audio data")

    return {
        "audio": audio,
        "subtitles": subtitles,
        "usage": usage,
        "event_counts": event_counts,
        "connect_request_id": connect_request_id,
        "connect_id": connect_id,
        "session_id": session_id,
        "request_params": request_params,
        "audio_chunk_count": len(audio_chunks),
    }


def validate_args(args: argparse.Namespace) -> None:
    if not -50 <= args.speech_rate <= 100:
        raise ValueError("--speech-rate must be between -50 and 100")
    if not -50 <= args.loudness_rate <= 100:
        raise ValueError("--loudness-rate must be between -50 and 100")
    if args.sample_rate not in {8000, 16000, 22050, 24000, 32000, 44100, 48000}:
        raise ValueError("Unsupported --sample-rate")
    if not 64000 <= args.bit_rate <= 160000:
        raise ValueError("--bit-rate must be between 64000 and 160000")
    if args.max_chars_per_request < 1:
        raise ValueError("--max-chars-per-request must be positive")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a single-speaker narration with Doubao TTS 2.0."
    )
    parser.add_argument("--text-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--record-file", type=Path)
    parser.add_argument("--subtitle-file", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env.local"))
    parser.add_argument("--api-key-env", default="DOUBAO_SPEECH_API_KEY")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--resource-id", default=DEFAULT_RESOURCE_ID)
    parser.add_argument("--speaker", default=DEFAULT_SPEAKER)
    parser.add_argument("--speaker-name", default=DEFAULT_SPEAKER_NAME)
    parser.add_argument("--model")
    parser.add_argument("--format", choices=("mp3", "pcm", "ogg_opus"), default="mp3")
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--bit-rate", type=int, default=128000)
    parser.add_argument("--speech-rate", type=int, default=0)
    parser.add_argument("--loudness-rate", type=int, default=0)
    parser.add_argument("--explicit-language", default="zh-cn")
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--no-subtitles", action="store_true")
    parser.add_argument("--max-chars-per-request", type=int, default=500)
    parser.add_argument("--request-interval-ms", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        validate_args(args)
        load_env_file(args.env_file)
        api_key = os.environ.get(args.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(
                f"Missing {args.api_key_env}; add it to {args.env_file} or the environment"
            )

        text = args.text_file.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"Narration file is empty: {args.text_file}")
        segments = split_text(text, args.max_chars_per_request)
        if not segments:
            raise ValueError("No narration segments were produced")

        result = asyncio.run(synthesize(args, api_key, segments))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(result.pop("audio"))

        if args.subtitle_file:
            args.subtitle_file.parent.mkdir(parents=True, exist_ok=True)
            args.subtitle_file.write_text(
                json.dumps(result["subtitles"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        record = {
            "provider": "volcengine-doubao-speech",
            "api": "bidirectional-websocket-tts-v3",
            "endpoint": args.endpoint,
            "resource_id": args.resource_id,
            "speaker": {"id": args.speaker, "name": args.speaker_name},
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_text_file": str(args.text_file),
            "output_audio_file": str(args.output),
            "text_character_count": len(text),
            "text_segment_count": len(segments),
            "audio_byte_count": args.output.stat().st_size,
            "api_key_environment_variable": args.api_key_env,
            "request": result["request_params"],
            "usage": result["usage"],
            "event_counts": result["event_counts"],
            "audio_chunk_count": result["audio_chunk_count"],
            "connect_request_id": result["connect_request_id"],
            "connect_id": result["connect_id"],
            "session_id": result["session_id"],
            "subtitle_file": str(args.subtitle_file) if args.subtitle_file else None,
            "documentation": "https://www.volcengine.com/docs/6561/2532486?lang=zh",
        }
        if args.record_file:
            args.record_file.parent.mkdir(parents=True, exist_ok=True)
            args.record_file.write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "bytes": record["audio_byte_count"],
                    "speaker": record["speaker"],
                    "segments": len(segments),
                    "usage": result["usage"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (
        OSError,
        ValueError,
        RuntimeError,
        asyncio.TimeoutError,
        WebSocketException,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
