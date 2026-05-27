"""Corpus loading and label schema for the trim tuning harness."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def content_hash(path: Path) -> str:
    """Return a sha256 hex digest of the file at *path*, prefixed with ``sha256:``."""
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{h}"


@dataclass(frozen=True)
class Label:
    """A single labelled fixture entry from ``labels.json``."""

    filename: str
    source_url: str
    ytdlp_version: str
    content_hash: str
    duration: float
    category: str  # threshold | wrong_gap | no_gap | clean
    true_start_max: float
    true_end_min: float
    true_start_min: float | None = None
    true_end_max: float | None = None

    @property
    def effective_start_min(self) -> float:
        """Lower bound for the true intro end; falls back to ``true_start_max`` when unset."""
        return self.true_start_max if self.true_start_min is None else self.true_start_min

    @property
    def effective_end_max(self) -> float:
        """Upper bound for the true outro start; falls back to ``true_end_min`` when unset."""
        return self.true_end_min if self.true_end_max is None else self.true_end_max


@dataclass(frozen=True)
class Fixture:
    """A validated corpus entry pairing a :class:`Label` with its local audio file."""

    label: Label
    audio_path: Path | None  # None when audio is absent locally
    hash_ok: bool


def load_corpus(corpus_dir: Path) -> list[Fixture]:
    """Load and validate ``labels.json``; emit a Fixture with ``audio_path=None`` when audio is absent."""
    entries = json.loads((corpus_dir / "labels.json").read_text())
    fixtures: list[Fixture] = []
    for entry in entries:
        label = Label(**entry)
        audio = corpus_dir / label.filename
        if not audio.exists():
            fixtures.append(Fixture(label=label, audio_path=None, hash_ok=False))
            continue
        ok = content_hash(audio) == label.content_hash
        fixtures.append(Fixture(label=label, audio_path=audio, hash_ok=ok))
    return fixtures
