#!/usr/bin/env python3
"""Stage one narration audio file on tmpfile.link for a Jianying draft."""

import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


API_URL = "https://tmpfile.link/api/upload"
MAX_FILE_BYTES = 100 * 1024 * 1024
RETENTION_SECONDS = 7 * 24 * 60 * 60


def run_curl(arguments: list[str], error_prefix: str) -> str:
    result = subprocess.run(arguments, check=False, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{error_prefix}: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def upload(source: Path) -> dict[str, Any]:
    raw = run_curl(
        [
            "curl",
            "--silent",
            "--show-error",
            "--fail-with-body",
            "--max-time",
            "120",
            "--request",
            "POST",
            "--form",
            f"file=@{source}",
            API_URL,
        ],
        "tmpfile.link upload failed",
    )
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("tmpfile.link upload returned invalid JSON") from exc
    if not isinstance(response, dict):
        raise RuntimeError("tmpfile.link upload returned an unexpected response")
    return response


def verify_fetchable(url: str) -> None:
    run_curl(
        [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--location",
            "--max-time",
            "30",
            "--range",
            "0-1",
            "--output",
            os.devnull,
            url,
        ],
        "tmpfile.link URL verification failed",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload one narration file to tmpfile.link and save its temporary HTTPS URL privately."
    )
    parser.add_argument("--input", type=Path, required=True, help="Local audio file to upload.")
    parser.add_argument("--record-file", type=Path, required=True, help="Private JSON record for the temporary URL.")
    args = parser.parse_args()

    source = args.input.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Audio file not found: {source}")
    size_bytes = source.stat().st_size
    if size_bytes <= 0:
        raise ValueError(f"Audio file is empty: {source}")
    if size_bytes > MAX_FILE_BYTES:
        raise ValueError(f"Audio file exceeds tmpfile.link 100 MB limit: {size_bytes} bytes")

    response = upload(source)
    url = str(response.get("downloadLinkEncoded") or response.get("downloadLink") or "").strip()
    if not url.startswith("https://"):
        raise RuntimeError("tmpfile.link response did not include a public HTTPS download URL")
    if response.get("uploadedTo") != "public":
        raise RuntimeError(f"Expected anonymous public upload, got: {response.get('uploadedTo')!r}")
    verify_fetchable(url)

    now = datetime.now(timezone.utc)
    record = {
        "provider": "tmpfile.link",
        "upload_endpoint": API_URL,
        "audio_file": str(source),
        "audio_byte_count": size_bytes,
        "audio_url": url,
        "access": "public to anyone holding the URL",
        "uploaded_at": now.isoformat(),
        "retention_seconds": RETENTION_SECONDS,
        "expected_expiration_at": (now + timedelta(seconds=RETENTION_SECONDS)).isoformat(),
        "early_deletion": "No documented anonymous early-deletion method; do not rely on early deletion.",
        "provider_response": {
            key: response.get(key)
            for key in ("fileName", "size", "type", "uploadedTo")
        },
    }
    args.record_file.parent.mkdir(parents=True, exist_ok=True)
    args.record_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "record_file": str(args.record_file),
                "expected_expiration_at": record["expected_expiration_at"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
