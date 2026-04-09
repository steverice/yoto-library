"""macOS Finder icon management: set and clear custom file icons."""

from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from yoto_lib import mka
from yoto_lib.icons.image import build_icns

# ── macOS icon setter ────────────────────────────────────────────────────────

_OSASCRIPT_TEMPLATE = """\
use framework "AppKit"
use scripting additions
set ws to current application's NSWorkspace's sharedWorkspace()
set img to current application's NSImage's alloc()'s initWithContentsOfFile:"{icns_path}"
ws's setIcon:img forFile:"{file_path}" options:0
"""


def _run_osascript(script: str) -> None:
    """Run an AppleScript via a temp file to avoid shell escaping issues."""
    with tempfile.NamedTemporaryFile(suffix=".scpt", mode="w", delete=False) as tmp:
        tmp.write(script)
        script_path = tmp.name
    try:
        subprocess.run(
            ["osascript", "-l", "AppleScript", script_path],
            capture_output=True,
            check=True,
        )
    finally:
        Path(script_path).unlink(missing_ok=True)


def set_macos_file_icon(file_path: Path, icon_16: Image.Image) -> None:
    """Set the macOS Finder icon for file_path using an ICNS built from icon_16."""
    icns_data = build_icns(icon_16)

    with tempfile.NamedTemporaryFile(suffix=".icns", delete=False) as tmp:
        tmp.write(icns_data)
        icns_path = tmp.name

    try:
        abs_file = str(Path(file_path).resolve()).replace('\\', '\\\\').replace('"', '\\"')
        script = _OSASCRIPT_TEMPLATE.format(
            icns_path=icns_path,
            file_path=abs_file,
        )
        _run_osascript(script)
    finally:
        Path(icns_path).unlink(missing_ok=True)


def clear_macos_file_icon(file_path: Path) -> None:
    """Remove the custom Finder icon from a file."""
    abs_file = str(Path(file_path).resolve()).replace('\\', '\\\\').replace('"', '\\"')
    script = (
        f'use framework "AppKit"\n'
        f'use scripting additions\n'
        f'set ws to current application\'s NSWorkspace\'s sharedWorkspace()\n'
        f'ws\'s setIcon:(missing value) forFile:"{abs_file}" options:0\n'
    )
    _run_osascript(script)


def apply_icon_to_mka(mka_path: Path, icon_data: bytes) -> None:
    """Attach icon PNG to MKA and set macOS Finder icon."""
    if mka_path.suffix.lower() == ".mka":
        icon_tmp = mka_path.parent / f".icon_tmp_{mka_path.stem}.png"
        try:
            icon_tmp.write_bytes(icon_data)
            mka.set_attachment(mka_path, icon_tmp, name="icon", mime_type="image/png")
        finally:
            icon_tmp.unlink(missing_ok=True)

    try:
        img = Image.open(io.BytesIO(icon_data))
        set_macos_file_icon(mka_path, img)
    except OSError:
        pass
