"""Scoring: hard clip-constraint + worst-case-leftover tiebreak, over a tolerance sweep."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from yoto_lib.tools.trim_tune.corpus import Label


@dataclass(frozen=True)
class FixtureScore:
    filename: str
    start_clip: float
    end_clip: float
    start_leftover: float
    end_leftover: float

    @property
    def clip(self) -> float:
        return max(self.start_clip, self.end_clip)

    @property
    def leftover(self) -> float:
        return self.start_leftover + self.end_leftover


def score_fixture(label: Label, predicted: tuple[float, float] | None) -> FixtureScore:
    """Compare a prediction to a label. None prediction => (0, duration) (no trim)."""
    ps, pe = (0.0, label.duration) if predicted is None else predicted
    return FixtureScore(
        filename=label.filename,
        start_clip=max(0.0, ps - label.true_start_max),
        end_clip=max(0.0, label.true_end_min - pe),
        start_leftover=max(0.0, label.effective_start_min - ps),
        end_leftover=max(0.0, pe - label.effective_end_max),
    )


@dataclass
class ConfigScore:
    params: dict
    fixtures: list[FixtureScore] = field(default_factory=list)

    @property
    def worst_clip(self) -> FixtureScore:
        return max(self.fixtures, key=lambda fs: fs.clip)

    def survives(self, tolerance: float) -> bool:
        return all(fs.clip <= tolerance for fs in self.fixtures)

    @property
    def worst_leftover(self) -> float:
        return max(fs.leftover for fs in self.fixtures)

    @property
    def total_leftover(self) -> float:
        return sum(fs.leftover for fs in self.fixtures)


def rank_survivors(configs: list[ConfigScore], tolerance: float) -> list[ConfigScore]:
    """Survivors (no clip beyond tolerance), ranked by worst-case then total leftover."""
    survivors = [c for c in configs if c.survives(tolerance)]
    return sorted(survivors, key=lambda c: (c.worst_leftover, c.total_leftover))
