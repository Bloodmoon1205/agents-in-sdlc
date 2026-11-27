"""Maps Jev's raw direction probability to an empirical win probability.

Binned with a Beta prior centred on a SHRUNK raw probability, so with little
data the map is deliberately pessimistic: p_cal = 0.5 + shrink * (p_raw - 0.5).
Updated nightly from labelled outcomes; never by the live loop.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Calibrator:
    shrink: float = 0.5
    prior_strength: float = 20.0
    bin_width: float = 0.05
    bins: dict = field(default_factory=dict)  # "0.85" -> [wins, n]

    def _key(self, p: float) -> str:
        lo = min(int(p / self.bin_width), int(1 / self.bin_width) - 1) * self.bin_width
        return f"{lo:.2f}"

    def map(self, p_raw: float) -> float:
        prior = 0.5 + self.shrink * (p_raw - 0.5)
        w, n = self.bins.get(self._key(p_raw), (0, 0))
        a, b = prior * self.prior_strength, (1 - prior) * self.prior_strength
        return (w + a) / (n + a + b)

    def update(self, p_raw: float, won: bool) -> None:
        k = self._key(p_raw)
        w, n = self.bins.get(k, (0, 0))
        self.bins[k] = [w + int(won), n + 1]

    @property
    def n(self) -> int:
        return sum(v[1] for v in self.bins.values())

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps({"shrink": self.shrink, "prior_strength": self.prior_strength,
                                          "bin_width": self.bin_width, "bins": self.bins}, indent=1))

    @classmethod
    def load(cls, path: Path) -> "Calibrator":
        p = Path(path)
        return cls(**json.loads(p.read_text())) if p.exists() else cls()
