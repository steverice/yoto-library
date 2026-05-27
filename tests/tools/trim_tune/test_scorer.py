"""Tests for trim_tune.scorer."""

from __future__ import annotations

from yoto_lib.tools.trim_tune.corpus import Label
from yoto_lib.tools.trim_tune.scorer import score_fixture


def _label(**kw):
    base = {
        "filename": "f",
        "source_url": "u",
        "ytdlp_version": "1",
        "content_hash": "h",
        "duration": 200.0,
        "category": "wrong_gap",
        "true_start_max": 8.0,
        "true_end_min": 190.0,
        "true_start_min": 6.0,
        "true_end_max": 192.0,
    }
    base.update(kw)
    return Label(**base)


def test_prediction_inside_zone_has_zero_clip_zero_leftover():
    fs = score_fixture(_label(), predicted=(7.0, 191.0))
    assert fs.start_clip == 0
    assert fs.end_clip == 0
    assert fs.start_leftover == 0
    assert fs.end_leftover == 0


def test_over_trim_start_records_clip_magnitude():
    fs = score_fixture(_label(), predicted=(8.5, 191.0))  # 0.5s past true_start_max
    assert abs(fs.start_clip - 0.5) < 1e-9
    assert fs.clip == fs.start_clip  # worst side


def test_under_trim_records_leftover():
    fs = score_fixture(_label(), predicted=(2.0, 195.0))  # left promo + tail
    assert abs(fs.start_leftover - 4.0) < 1e-9  # 6.0 - 2.0
    assert abs(fs.end_leftover - 3.0) < 1e-9  # 195 - 192


def test_none_prediction_maps_to_full_track_never_clips():
    fs = score_fixture(
        _label(category="clean", true_start_max=0.1, true_end_min=199.9, true_start_min=0.0, true_end_max=200.0),
        predicted=None,
    )
    assert fs.clip == 0  # no trim cannot clip
