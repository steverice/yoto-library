"""Shared configuration constants for yoto_lib."""

from __future__ import annotations

import os

WORKERS = int(os.environ.get("YOTO_WORKERS", "4"))
