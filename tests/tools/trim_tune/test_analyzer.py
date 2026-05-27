"""Tests for trim_tune.analyzer."""

from __future__ import annotations

from yoto_lib.tools.trim_tune.analyzer import CachingAnalyzer


def test_silence_ranges_cached_per_signature(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    calls = []

    class FakeInner:
        def silence_ranges(self, path, *, noise_db, min_duration):
            calls.append((noise_db, min_duration))
            return [(0.0, 1.0)]

        def duration(self, path):
            return 10.0

    a = CachingAnalyzer(inner=FakeInner(), ffmpeg_version="8.0")
    a.silence_ranges(audio, noise_db=-30, min_duration=0.5)
    a.silence_ranges(audio, noise_db=-30, min_duration=0.5)  # cache hit
    a.silence_ranges(audio, noise_db=-40, min_duration=0.5)  # new signature
    assert calls == [(-30, 0.5), (-40, 0.5)]


def test_duration_cached_per_path(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    calls = []

    class FakeInner:
        def silence_ranges(self, path, *, noise_db, min_duration):
            return []

        def duration(self, path):
            calls.append(path)
            return 10.0

    a = CachingAnalyzer(inner=FakeInner(), ffmpeg_version="8.0")
    assert a.duration(audio) == 10.0
    assert a.duration(audio) == 10.0  # cache hit
    assert len(calls) == 1
