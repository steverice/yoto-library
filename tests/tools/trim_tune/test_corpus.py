"""Tests for trim_tune.corpus."""

from __future__ import annotations

import json

from yoto_lib.tools.trim_tune.corpus import Label, content_hash, load_corpus


def _write_labels(corpus_dir, entries):
    (corpus_dir / "labels.json").write_text(json.dumps(entries))


def test_label_optional_bounds_default_to_clip_bounds():
    label = Label(
        filename="a.opus",
        source_url="u",
        ytdlp_version="1",
        content_hash="h",
        duration=100.0,
        category="clean",
        true_start_max=2.0,
        true_end_min=98.0,
        true_start_min=None,
        true_end_max=None,
    )
    assert label.effective_start_min == 2.0  # falls back to true_start_max
    assert label.effective_end_max == 98.0  # falls back to true_end_min


def test_load_corpus_skips_absent_audio(tmp_path):
    _write_labels(
        tmp_path,
        [
            {
                "filename": "missing.opus",
                "source_url": "u",
                "ytdlp_version": "1",
                "content_hash": "h",
                "duration": 100.0,
                "category": "clean",
                "true_start_max": 0.1,
                "true_end_min": 99.9,
            }
        ],
    )
    loaded = load_corpus(tmp_path)
    assert loaded[0].audio_path is None  # skipped, label still present


def test_load_corpus_flags_hash_mismatch(tmp_path):
    audio = tmp_path / "song.opus"
    audio.write_bytes(b"abc")
    _write_labels(
        tmp_path,
        [
            {
                "filename": "song.opus",
                "source_url": "u",
                "ytdlp_version": "1",
                "content_hash": "sha256:WRONG",
                "duration": 100.0,
                "category": "clean",
                "true_start_max": 0.1,
                "true_end_min": 99.9,
            }
        ],
    )
    loaded = load_corpus(tmp_path)
    assert loaded[0].hash_ok is False


def test_content_hash_has_sha256_prefix(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc")
    h = content_hash(f)
    assert h.startswith("sha256:")
    assert len(h) > len("sha256:")
