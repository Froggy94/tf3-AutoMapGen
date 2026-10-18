"""Real elevations as game heights of one climate mod (reference height + scale)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GameHeights:
    """Game height = (real elevation - reference height) x game metres per real metre, rounded to 0.1 m."""

    reference_m: float
    game_per_real: float

    def of(self, elevation_m: float) -> float:
        return round((elevation_m - self.reference_m) * self.game_per_real, 1)
