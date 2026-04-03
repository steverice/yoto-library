"""Validate that Yoto's transcode pipeline accepts MKA containers.

Usage:
    python validation/validate_mka_transcode.py <any-audio-file>

What it does:
    1. Wraps the source audio in an MKA (Matroska Audio) container using FFmpeg
       with stream-copy (no re-encode: ffmpeg -y -i source -c copy output.mka)
    2. Authenticates via the Yoto device-code flow (caches token in Keychain)
    3. Uploads the MKA through Yoto's transcode pipeline
    4. Reports success (transcodedSha256 + transcodedInfo) or failure

Exit codes:
    0  transcode succeeded
    1  transcode failed or pipeline error
    2  bad arguments / ffmpeg not found
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

# Allow running from the repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from yoto_lib.api import YotoAPI, YotoAPIError
from yoto_lib.auth import AuthError, get_valid_token


def wrap_in_mka(source: Path, dest: Path) -> None:
    """Stream-copy source audio into an MKA container using FFmpeg."""
    cmd = [
        "ffmpeg",
        "-y",          # overwrite output without asking
        "-i", str(source),
        "-c", "copy",  # no re-encode
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("FFmpeg stderr:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(
            f"FFmpeg exited with code {result.returncode} wrapping '{source}' -> '{dest}'"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test whether Yoto's transcode pipeline accepts MKA containers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "audio_file",
        metavar="<audio-file>",
        help="Source audio file (mp3, wav, flac, m4a, etc.)",
    )
    args = parser.parse_args()

    source = Path(args.audio_file)
    if not source.exists():
        print(f"Error: file not found: {source}", file=sys.stderr)
        return 2
    if not source.is_file():
        print(f"Error: not a file: {source}", file=sys.stderr)
        return 2

    # Check ffmpeg is available
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("Error: 'ffmpeg' not found. Install it and ensure it is on PATH.", file=sys.stderr)
        return 2

    # Authenticate (uses cached token if still valid)
    print("Checking authentication...")
    try:
        get_valid_token(interactive=True)
        print("Authenticated.")
    except AuthError as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 1

    # Wrap source in MKA using a temp file
    with tempfile.TemporaryDirectory(prefix="yoto_mka_") as tmp_dir:
        mka_path = Path(tmp_dir) / (source.stem + ".mka")

        print(f"Wrapping '{source.name}' -> '{mka_path.name}' ...")
        try:
            wrap_in_mka(source, mka_path)
        except RuntimeError as exc:
            print(f"FFmpeg error: {exc}", file=sys.stderr)
            return 1

        print(f"MKA created: {mka_path.stat().st_size:,} bytes")

        # Upload and transcode via Yoto pipeline
        print("Uploading MKA to Yoto transcode pipeline...")
        try:
            api = YotoAPI(interactive=True)
            result = api.upload_and_transcode(mka_path)
        except YotoAPIError as exc:
            print(f"\nFAILURE: Transcode pipeline error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"\nFAILURE: Unexpected error: {exc}", file=sys.stderr)
            return 1

    # Success
    print("\nSUCCESS: MKA transcode accepted by Yoto pipeline.")
    print(f"  transcodedSha256 : {result.get('transcodedSha256', '(not returned)')}")
    transcoded_info = result.get("transcodedInfo") or result.get("transcodedinfo")
    if transcoded_info:
        print(f"  transcodedInfo   : {transcoded_info}")
    else:
        # Print whatever keys came back for diagnostics
        for key, value in result.items():
            if key != "transcodedSha256":
                print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
