"""Tests for trim_tune.labeler."""

from __future__ import annotations

import json

from yoto_lib.tools.trim_tune.labeler import upsert_label


def test_upsert_label_fills_hash_and_duration(tmp_path, monkeypatch):
    audio = tmp_path / "song.opus"
    audio.write_bytes(b"abc")
    monkeypatch.setattr("yoto_lib.tools.trim_tune.labeler._probe_duration", lambda p: 123.4)
    monkeypatch.setattr("yoto_lib.tools.trim_tune.labeler._ytdlp_version", lambda: "2026.05.01")
    upsert_label(
        tmp_path,
        filename="song.opus",
        source_url="u",
        category="wrong_gap",
        true_start_max=8.0,
        true_end_min=120.0,
        true_start_min=6.0,
        true_end_max=122.0,
    )
    entry = json.loads((tmp_path / "labels.json").read_text())[0]
    assert entry["duration"] == 123.4
    assert entry["ytdlp_version"] == "2026.05.01"
    assert entry["content_hash"].startswith("sha256:")


def test_upsert_label_replaces_existing_entry(tmp_path, monkeypatch):
    audio = tmp_path / "song.opus"
    audio.write_bytes(b"abc")
    monkeypatch.setattr("yoto_lib.tools.trim_tune.labeler._probe_duration", lambda p: 100.0)
    monkeypatch.setattr("yoto_lib.tools.trim_tune.labeler._ytdlp_version", lambda: "v1")
    upsert_label(
        tmp_path, filename="song.opus", source_url="u", category="clean", true_start_max=1.0, true_end_min=99.0
    )
    upsert_label(
        tmp_path, filename="song.opus", source_url="u", category="wrong_gap", true_start_max=2.0, true_end_min=98.0
    )
    entries = json.loads((tmp_path / "labels.json").read_text())
    assert len(entries) == 1  # replaced, not appended
    assert entries[0]["category"] == "wrong_gap"
