"""Tests for trim_tune.verifier."""

from __future__ import annotations

import shutil

from yoto_lib.tools.trim_tune.verifier import measure_cut


def test_measure_cut_reports_actual_shipped_duration(longer_wav, tmp_path):
    """measure_cut runs apply_cut on a COPY and reports the real resulting duration."""
    work = tmp_path / "copy.wav"
    shutil.copy(longer_wav, work)
    measured = measure_cut(work, start=0.25, end=0.75)  # ~0.5s
    assert abs(measured - 0.5) < 0.1
    assert work.exists()  # copy is consumed/replaced, not the original fixture
