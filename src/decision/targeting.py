from __future__ import annotations

import os
import re
from dataclasses import dataclass
from math import sqrt
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Target:
    cls: str
    confidence: float
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> Tuple[float, float]:
        return (self.x, self.y)


def _norm_label(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^a-z0-9_]+", "", s)
    s = re.sub(r"_+", "_", s)
    return s


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


class TargetSelector:
    """Selecciona un target estable desde detecciones (Roboflow boxes).

    Heurística:
    - Filtra por confianza mínima.
    - Ignora clases no relevantes (configurable).
    - (Opcional) aplica prioridad por clases.
    - Score = confidence - center_weight * dist_to_center_norm.
    - Sticky: mantiene el target anterior si sigue presente y no es claramente peor.

    Config por env vars:
    - TARGET_MIN_CONF (default 0.25)
    - TARGET_CENTER_WEIGHT (default 0.15)
    - TARGET_SWITCH_DELTA (default 0.05)
    - TARGET_MATCH_MAX_DIST_NORM (default 0.15)
    - TARGET_PRIORITY_CLASSES (default "")
    - TARGET_IGNORE_CLASSES (default "hp_text,mp_text,hp_ocr,mp_ocr,hp_bar,mp_bar,health_bar,mana_bar")
    """

    def __init__(self) -> None:
        self.min_conf = float(os.getenv("TARGET_MIN_CONF", "0.25"))
        self.center_weight = float(os.getenv("TARGET_CENTER_WEIGHT", "0.15"))
        self.switch_delta = float(os.getenv("TARGET_SWITCH_DELTA", "0.05"))
        self.match_max_dist_norm = float(os.getenv("TARGET_MATCH_MAX_DIST_NORM", "0.15"))

        prio_raw = os.getenv("TARGET_PRIORITY_CLASSES", "").strip()
        self.priority: Tuple[str, ...] = tuple(
            _norm_label(p) for p in (x.strip() for x in prio_raw.split(",")) if p
        )

        ignore_raw = os.getenv(
            "TARGET_IGNORE_CLASSES",
            "hp_text,mp_text,hp_ocr,mp_ocr,hp_bar,mp_bar,health_bar,mana_bar",
        )
        self.ignore = { _norm_label(x) for x in (p.strip() for p in ignore_raw.split(",")) if x }

    def _iter_targets(self, boxes: Iterable[Dict[str, Any]]) -> Iterable[Target]:
        for b in boxes:
            if not isinstance(b, dict):
                continue
            cls = _norm_label(str(b.get("class", "")))
            if not cls or cls in self.ignore:
                continue
            conf = _safe_float(b.get("confidence", 0.0), 0.0)
            if conf < self.min_conf:
                continue
            x = _safe_float(b.get("x", 0.0), 0.0)
            y = _safe_float(b.get("y", 0.0), 0.0)
            w = max(1e-6, _safe_float(b.get("width", 0.0), 0.0))
            h = max(1e-6, _safe_float(b.get("height", 0.0), 0.0))
            yield Target(cls=cls, confidence=conf, x=x, y=y, width=w, height=h)

    @staticmethod
    def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

    def _score(self, t: Target, resolution: Tuple[int, int]) -> float:
        w, h = resolution
        cx, cy = (w / 2.0), (h / 2.0)
        diag = max(1.0, sqrt(w * w + h * h))
        d_norm = self._dist(t.center, (cx, cy)) / diag
        return float(t.confidence) - float(self.center_weight) * float(d_norm)

    def _pick_best(self, cands: Sequence[Target], resolution: Tuple[int, int]) -> Optional[Target]:
        if not cands:
            return None
        best = None
        best_score = -1e9
        for t in cands:
            sc = self._score(t, resolution)
            if sc > best_score:
                best_score = sc
                best = t
        return best

    def _match_prev(self, prev: Target, cands: Sequence[Target], resolution: Tuple[int, int]) -> Optional[Target]:
        # match by same class and nearest center to previous center
        same_cls = [t for t in cands if t.cls == prev.cls]
        if not same_cls:
            return None

        w, h = resolution
        diag = max(1.0, sqrt(w * w + h * h))
        max_dist = float(self.match_max_dist_norm) * diag

        best = None
        best_d = 1e18
        for t in same_cls:
            d = self._dist(t.center, prev.center)
            if d < best_d:
                best_d = d
                best = t

        if best is None:
            return None
        if best_d > max_dist:
            return None
        return best

    def select_target(
        self,
        boxes: Optional[Sequence[Dict[str, Any]]],
        resolution: Tuple[int, int],
        prev: Optional[Target] = None,
    ) -> Optional[Target]:
        if not boxes:
            return None

        cands = list(self._iter_targets(boxes))
        if not cands:
            return None

        # Apply priority: pick best within first present class.
        if self.priority:
            for cls in self.priority:
                cls_cands = [t for t in cands if t.cls == cls]
                if cls_cands:
                    best = self._pick_best(cls_cands, resolution)
                    # still allow sticky within same class (below)
                    cands = cls_cands
                    break

        best_now = self._pick_best(cands, resolution)
        if best_now is None:
            return None

        if prev is None:
            return best_now

        prev_match = self._match_prev(prev, cands, resolution)
        if prev_match is None:
            return best_now

        # Sticky decision
        best_score = self._score(best_now, resolution)
        prev_score = self._score(prev_match, resolution)
        if prev_score + float(self.switch_delta) >= best_score:
            return prev_match

        return best_now


def format_target(t: Optional[Target]) -> str:
    if t is None:
        return "none"
    return f"{t.cls} ({t.confidence:.2f})"
