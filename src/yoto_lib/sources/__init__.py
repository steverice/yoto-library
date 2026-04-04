"""Source providers — resolve .webloc URLs into audio files."""

from __future__ import annotations

import plistlib
from pathlib import Path


def parse_webloc(path: Path) -> str | None:
    """Extract the URL from a .webloc plist file. Returns None on failure."""
    try:
        data = plistlib.loads(path.read_bytes())
        return data.get("URL")
    except Exception:
        return None
