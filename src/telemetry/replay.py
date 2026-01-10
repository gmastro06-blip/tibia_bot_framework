from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import numpy as np


def _safe_mkdir(p: Path) -> None:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _json_default(o: object) -> object:
    # Best-effort serialization for dataclasses/ndarrays
    try:
        if hasattr(o, "__dict__"):
            return dict(getattr(o, "__dict__"))
    except Exception:
        pass
    return str(o)


def _parse_ts_from_filename(name: str) -> float | None:
    try:
        stem = Path(name).stem
        # JSON: "123.456789" ; PNG: "123.456789_hp_top_ocr"
        head = stem.split("_", 1)[0]
        return float(head)
    except Exception:
        return None


def _prune_replays(base: Path, *, keep_json: int) -> None:
    try:
        if keep_json <= 0:
            return
        json_files = [p for p in base.glob("*.json") if p.is_file()]
        if len(json_files) <= keep_json:
            return

        def key(p: Path):
            ts = _parse_ts_from_filename(p.name)
            return (ts if ts is not None else 0.0)

        json_files.sort(key=key)
        to_delete = json_files[: max(0, len(json_files) - keep_json)]

        rois_dir = base / "rois"
        for jf in to_delete:
            ts = _parse_ts_from_filename(jf.name)
            try:
                jf.unlink(missing_ok=True)
            except Exception:
                pass

            if ts is None:
                continue

            # Borra PNGs que correspondan a ese timestamp.
            try:
                prefix = f"{ts:.6f}_"
                for png in rois_dir.glob(f"{prefix}*.png"):
                    try:
                        png.unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass


class ReplayRecorder:
    """Guarda snapshots (JSON + crops de ROIs) en disco.

    Diseñado para ser llamado desde el thread de visión (tiene acceso al frame).
    """

    def __init__(self) -> None:
        self._last_ts = 0.0

    def should_record(self, *, enabled: bool, interval_ms: int) -> bool:
        if not enabled:
            return False
        now = time.time()
        interval_s = max(0.05, float(interval_ms) / 1000.0)
        ok = (now - self._last_ts) >= interval_s
        if ok:
            # Marca el tick aquí para permitir throttling incluso si el caller
            # decide no grabar finalmente.
            self._last_ts = now
        return ok

    def record_crops(
        self,
        *,
        out_dir: str,
        crops: Mapping[str, np.ndarray],
        payload: Mapping[str, Any],
        ts: Optional[float] = None,
    ) -> None:
        t = time.time() if ts is None else float(ts)
        base = Path(out_dir)
        _safe_mkdir(base)
        rois_dir = base / "rois"
        _safe_mkdir(rois_dir)

        # JSON snapshot
        json_path = base / f"{t:.6f}.json"
        try:
            json_path.write_text(json.dumps(dict(payload), ensure_ascii=False, default=_json_default), encoding="utf-8")
        except Exception:
            pass

        # PNG crops
        try:
            import cv2

            for name, img in crops.items():
                if img is None:
                    continue
                if not isinstance(img, np.ndarray):
                    continue
                if img.size == 0:
                    continue
                p = rois_dir / f"{t:.6f}_{name}.png"
                try:
                    cv2.imwrite(str(p), img)
                except Exception:
                    pass
        except Exception:
            # cv2 no disponible o falla: igual dejamos el JSON.
            pass

        self._last_ts = t

        # Pruning opcional para evitar crecimiento infinito.
        try:
            raw = os.getenv("REPLAY_MAX_JSON", "").strip()
            if raw:
                keep = int(raw)
                _prune_replays(base, keep_json=keep)
        except Exception:
            pass


def crop_named_rois(
    frame: np.ndarray,
    *,
    roi_to_px,
    rois: Dict[str, Dict[str, float]],
    resolution: tuple[int, int],
    names: list[str],
) -> Dict[str, np.ndarray]:
    """Corta ROIs desde un frame usando un conversor roi_to_px compatible con OCRProcessor._roi_to_px."""

    crops: Dict[str, np.ndarray] = {}
    for name in names:
        if name not in rois:
            continue
        try:
            x, y, w, h = roi_to_px(frame, rois, resolution, rois[name])
            x0 = max(0, int(x))
            y0 = max(0, int(y))
            x1 = min(int(frame.shape[1]), x0 + max(1, int(w)))
            y1 = min(int(frame.shape[0]), y0 + max(1, int(h)))
            crop = frame[y0:y1, x0:x1].copy()
            crops[name] = crop
        except Exception:
            continue
    return crops


def default_replay_roi_names() -> list[str]:
    # Mantener corto: lo más útil para depurar visión/señales.
    return [
        "hpmp_top_strip",
        "hp_top_ocr",
        "mp_top_ocr",
        "hp_low_bar",
        "mp_low_bar",
        "states_icons",
        "equipment_slots",
        "skills_panel",
        "right_hud_panel",
        "hpmp_low_panel",
        # Optional fine-grain ROIs (only recorded if present in the config):
        "amulet_slot",
        "ring_slot",
        "cap_ocr",
        "soul_ocr",
        "items_status_bar",
        "minimap_content",
        "battlelist_rows",
        "chat_panel",
        "coords_ocr",
    ]


def env_replay_enabled() -> bool:
    return os.getenv("REPLAY_ENABLED", "").strip().lower() in {"1", "true", "yes"}
