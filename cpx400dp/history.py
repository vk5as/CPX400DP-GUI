"""Bounded in-memory history of readings, for the live chart and CSV export.

Owned and mutated by the GUI thread only (it's fed from evt_q events, not
from the worker directly), so no locking is needed here.
"""

from __future__ import annotations

import csv
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sample:
    t: float  # time.monotonic() timestamp
    v1: float
    i1: float
    v2: float
    i2: float


class History:
    def __init__(self, max_points: int = 7200):
        self.max_points = max_points
        self._samples: deque[Sample] = deque(maxlen=max_points)
        self._t0 = time.monotonic()

    def clear(self) -> None:
        self._samples.clear()
        self._t0 = time.monotonic()

    def add(self, v1: float, i1: float, v2: float, i2: float, t: float | None = None) -> None:
        self._samples.append(Sample(t=t if t is not None else time.monotonic(), v1=v1, i1=i1, v2=v2, i2=i2))

    def __len__(self) -> int:
        return len(self._samples)

    def since(self, window_s: float | None) -> list[Sample]:
        """Return samples within the last `window_s` seconds (None = all)."""
        if not self._samples:
            return []
        if window_s is None:
            return list(self._samples)
        cutoff = self._samples[-1].t - window_s
        return [s for s in self._samples if s.t >= cutoff]

    @staticmethod
    def decimate(samples: list[Sample], max_points: int = 2000) -> list[Sample]:
        """Down-sample for plotting so a long history can't slow the
        redraw. Keeps the first and last sample and evenly-spaced points in
        between."""
        n = len(samples)
        if n <= max_points:
            return samples
        step = n / max_points
        indices = [int(i * step) for i in range(max_points)]
        indices[-1] = n - 1
        return [samples[i] for i in indices]

    def export_csv(self, path: Path) -> int:
        """Write the full retained buffer to CSV. Returns the row count."""
        samples = list(self._samples)
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp_iso", "t_rel_s", "v1", "i1", "p1", "v2", "i2", "p2"])
            wall_now = time.time()
            mono_now = time.monotonic()
            for s in samples:
                wall_t = wall_now - (mono_now - s.t)
                iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(wall_t))
                t_rel = s.t - self._t0
                writer.writerow([iso, f"{t_rel:.3f}", s.v1, s.i1, s.v1 * s.i1, s.v2, s.i2, s.v2 * s.i2])
        return len(samples)
