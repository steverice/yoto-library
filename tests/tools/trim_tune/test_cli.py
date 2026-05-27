"""End-to-end smoke test for `python -m yoto_lib.tools.trim_tune sweep`."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def _tone_corpus(corpus_dir: Path):
    """Build a 1-fixture corpus with a real (tone) audio file and a matching label."""
    corpus_dir.mkdir(parents=True)
    wav = corpus_dir / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(wav)],
        capture_output=True,
        check=True,
    )
    h = "sha256:" + hashlib.sha256(wav.read_bytes()).hexdigest()
    (corpus_dir / "labels.json").write_text(
        json.dumps(
            [
                {
                    "filename": "tone.wav",
                    "source_url": "u",
                    "ytdlp_version": "1",
                    "content_hash": h,
                    "duration": 2.0,
                    "category": "clean",
                    "true_start_max": 0.1,
                    "true_end_min": 1.9,
                    "true_start_min": 0.0,
                    "true_end_max": 2.0,
                }
            ]
        )
    )


def test_sweep_runs_and_reports(tmp_path):
    _tone_corpus(tmp_path / "corpus")
    repo_root = Path(__file__).resolve().parents[3]  # tests/tools/trim_tune/ -> repo root
    result = subprocess.run(
        [sys.executable, "-m", "yoto_lib.tools.trim_tune", "sweep", "--corpus", str(tmp_path / "corpus"), "--top", "3"],
        capture_output=True,
        text=True,
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": "src"},  # our PYTHONPATH wins over the parent's
    )
    assert result.returncode == 0, result.stderr
    assert "tolerance" in result.stdout.lower()


def _unusable_corpus(corpus_dir: Path):
    """Build a corpus whose audio file does NOT exist on disk (so the fixture is unusable)."""
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "labels.json").write_text(
        json.dumps(
            [
                {
                    "filename": "missing.wav",
                    "source_url": "u",
                    "ytdlp_version": "1",
                    "content_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                    "duration": 2.0,
                    "category": "clean",
                    "true_start_max": 0.1,
                    "true_end_min": 1.9,
                    "true_start_min": None,
                    "true_end_max": None,
                }
            ]
        )
    )


def test_sweep_empty_corpus_exits_cleanly(tmp_path):
    _unusable_corpus(tmp_path / "corpus")
    repo_root = Path(__file__).resolve().parents[3]  # tests/tools/trim_tune/ -> repo root
    result = subprocess.run(
        [sys.executable, "-m", "yoto_lib.tools.trim_tune", "sweep", "--corpus", str(tmp_path / "corpus")],
        capture_output=True,
        text=True,
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": "src"},  # our PYTHONPATH wins over the parent's
    )
    assert result.returncode == 0, result.stderr
    combined = (result.stdout + result.stderr).lower()
    assert "no usable fixtures" in combined
