from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from navigation.route import Waypoint


@dataclass(frozen=True)
class CoordSample:
    x: int
    y: int
    z: Optional[int]
    ts: float
    hp_current: Optional[int] = None
    mp_current: Optional[int] = None


class CoordsRecorder:
    """Records player coordinates over time and exports cavebot routes.

    This is intentionally simple and safe: it only records coordinates that are
    provided to it (e.g. from env vars or experimental minimap-motion), and writes a route
    JSON compatible with `navigation.route.load_route()`.

    Features:
    - optional z (floor) support
    - drop consecutive duplicates
    - minimum manhattan distance threshold to reduce noise
    - optional minimum time interval between points
    """

    def __init__(
        self,
        *,
        min_manhattan: int = 1,
        min_interval_s: float = 0.0,
        max_points: int = 10_000,
    ) -> None:
        self.min_manhattan = max(0, int(min_manhattan))
        self.min_interval_s = max(0.0, float(min_interval_s))
        self.max_points = max(1, int(max_points))
        self._samples: list[CoordSample] = []

    @property
    def samples(self) -> list[CoordSample]:
        return list(self._samples)

    def _last(self) -> CoordSample | None:
        return self._samples[-1] if self._samples else None

    def maybe_add(
        self,
        *,
        x: int,
        y: int,
        z: int | None = None,
        ts: float | None = None,
        hp_current: int | None = None,
        mp_current: int | None = None,
    ) -> bool:
        """Add a new point if it passes dedupe/threshold checks.

        Returns True if a point was added.
        """

        t = time.time() if ts is None else float(ts)
        last = self._last()

        if last is not None:
            if self.min_interval_s > 0.0 and (t - last.ts) < self.min_interval_s:
                return False

            # Drop consecutive duplicates (including z).
            if int(x) == int(last.x) and int(y) == int(last.y) and (z if z is not None else None) == last.z:
                return False

            # Distance threshold (ignore z for distance; floor changes are kept by the duplicate check).
            dist = abs(int(x) - int(last.x)) + abs(int(y) - int(last.y))
            if dist < self.min_manhattan:
                return False

        if len(self._samples) >= self.max_points:
            # Hard cap: keep the most recent points.
            self._samples.pop(0)

        self._samples.append(
            CoordSample(
                x=int(x),
                y=int(y),
                z=(int(z) if z is not None else None),
                ts=t,
                hp_current=(int(hp_current) if hp_current is not None else None),
                mp_current=(int(mp_current) if mp_current is not None else None),
            )
        )
        return True

    def save_samples_jsonl(self, path: str | Path, *, ensure_parent: bool = True) -> Path:
        """Save raw recorded samples as JSONL (one sample per line)."""

        p = Path(path)
        if ensure_parent:
            p.parent.mkdir(parents=True, exist_ok=True)

        import json

        with p.open("w", encoding="utf-8") as f:
            for s in self._samples:
                f.write(
                    json.dumps(
                        {
                            "ts": float(s.ts),
                            "x": int(s.x),
                            "y": int(s.y),
                            "z": (int(s.z) if s.z is not None else None),
                            "hp_current": (int(s.hp_current) if s.hp_current is not None else None),
                            "mp_current": (int(s.mp_current) if s.mp_current is not None else None),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        return p

    def to_waypoints(self, *, name_prefix: str = "wp", action: str | None = None) -> list[Waypoint]:
        out: list[Waypoint] = []
        for i, s in enumerate(self._samples):
            out.append(
                Waypoint(
                    x=int(s.x),
                    y=int(s.y),
                    z=(int(s.z) if s.z is not None else None),
                    name=f"{name_prefix}{i:04d}",
                    action=(str(action) if action else None),
                )
            )
        return out

    def to_route_jsonable(self, *, name_prefix: str = "wp", action: str | None = None) -> list[dict]:
        out: list[dict] = []
        for wp in self.to_waypoints(name_prefix=name_prefix, action=action):
            d: dict = {"x": wp.x, "y": wp.y}
            if wp.z is not None:
                d["z"] = wp.z
            if wp.name is not None:
                d["name"] = wp.name
            if wp.action is not None:
                d["action"] = wp.action
            out.append(d)
        return out

    def save_route(
        self,
        path: str | Path,
        *,
        name_prefix: str = "wp",
        action: str | None = None,
        ensure_parent: bool = True,
    ) -> Path:
        p = Path(path)
        if ensure_parent:
            p.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_route_jsonable(name_prefix=name_prefix, action=action)
        p.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return p

    @staticmethod
    def from_waypoints(waypoints: Iterable[Waypoint]) -> "CoordsRecorder":
        rec = CoordsRecorder(min_manhattan=0)
        for wp in waypoints:
            rec.maybe_add(x=wp.x, y=wp.y, z=wp.z)
        return rec
