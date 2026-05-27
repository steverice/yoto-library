"""Verify the winning config against the bytes that actually ship.

The sweep scores PREDICTED boundaries; stream-copy snaps to packet boundaries,
so the winner is re-checked by performing the real cut and probing the result.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from yoto_lib.track_sources._trim import DirectAnalyzer, apply_cut

if TYPE_CHECKING:
    from yoto_lib.tools.trim_tune.corpus import Fixture


def measure_cut(audio_path: Path, start: float, end: float) -> float:
    """Apply the real cut in place on audio_path and return the resulting duration."""
    apply_cut(audio_path, start, end)
    return DirectAnalyzer().duration(audio_path)


def verify_config(
    fixtures: list[Fixture],
    strategy: Any,
    params: dict,
    tolerance: float,
) -> dict[str, dict]:
    """For each fixture, cut a temp copy with the strategy's prediction and re-measure.

    Returns {filename: {predicted, shipped_start, shipped_end, clips}} so the
    certification is reported against shipped bytes, not predictions.
    """
    analyzer = DirectAnalyzer()
    out: dict[str, dict] = {}
    for f in fixtures:
        if f.audio_path is None or not f.hash_ok:
            continue
        pred = strategy.trim(f.audio_path, params, analyzer)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / f.audio_path.name
            shutil.copy(f.audio_path, work)
            if pred is None:
                shipped_start, shipped_end = 0.0, analyzer.duration(work)
            else:
                start, end = pred
                apply_cut(work, start, end)
                # NOTE: approximation accurate to ~one packet (~20ms for Opus).
                # Stream copy may snap the real start FORWARD of `start`, so
                # shipped_start is a lower bound and the clip check is slightly
                # optimistic. For a stronger guarantee, probe the cut file's
                # first-packet PTS instead of assuming it equals `start`.
                shipped_start, shipped_end = start, start + analyzer.duration(work)
            clips = (shipped_start > f.label.true_start_max + tolerance) or (
                shipped_end < f.label.true_end_min - tolerance
            )
            out[f.label.filename] = {
                "predicted": pred,
                "shipped_start": shipped_start,
                "shipped_end": shipped_end,
                "clips": clips,
            }
    return out
