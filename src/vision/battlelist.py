from __future__ import annotations

from collections import defaultdict
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

import numpy as np

from vision.ocr import OCRProcessor


# Public types (dict-based to keep it simple and JSONL-friendly)
BattlelistEntry = Dict[str, Any]


_OCR_SINGLETON: OCRProcessor | None = None
_OCR_CORRECTIONS: Dict[str, str] | None = None


def _get_repo_root() -> Path:
    # src/vision/battlelist.py -> parents: vision (0), src (1), repo (2)
    return Path(__file__).resolve().parents[2]


def _load_corrections() -> Dict[str, str]:
    global _OCR_CORRECTIONS
    if _OCR_CORRECTIONS is not None:
        return _OCR_CORRECTIONS

    # Reuse the same corrections file as OCRProcessor (but keep battlelist independent)
    p = (os.getenv("BATTLELIST_OCR_CORRECTIONS_PATH", "") or "").strip()
    if not p:
        p = str(_get_repo_root() / "configs" / "ocr_corrections.json")

    out: Dict[str, str] = {}
    try:
        import json

        out = json.loads(Path(p).read_text(encoding="utf-8"))
        if not isinstance(out, dict):
            out = {}
    except Exception:
        out = {}

    # Normalize keys/values
    norm: Dict[str, str] = {}
    for k, v in (out or {}).items():
        try:
            kk = str(k).strip().lower()
            vv = str(v).strip().lower()
            if kk and vv:
                norm[kk] = vv
        except Exception:
            continue

    _OCR_CORRECTIONS = norm
    return norm


def _get_ocr() -> OCRProcessor | None:
    global _OCR_SINGLETON
    if _OCR_SINGLETON is not None:
        return _OCR_SINGLETON
    try:
        _OCR_SINGLETON = OCRProcessor()
    except Exception:
        _OCR_SINGLETON = None
    return _OCR_SINGLETON


def _normalize_name(text: str) -> tuple[str, str]:
    """Return (name_norm, display).

    - name_norm: lowercased canonical key (used for stabilization).
    - display: human-friendly form (used for overlay/telemetry).
    """

    s = str(text or "").strip()
    if not s:
        return "", ""

    # Common OCR confusions for creature names
    s2 = s.strip()
    # Example: "0rc" -> "Orc"
    s2 = s2.replace("0", "o")
    s2 = " ".join(s2.split())

    key = s2.lower()

    # Apply shared OCR corrections mapping (if present)
    try:
        corr = _load_corrections()
        key = corr.get(key, key)
    except Exception:
        pass

    # Display: title-case but preserve hyphens/underscores reasonably
    disp = key.replace("_", " ")
    disp = " ".join([w for w in disp.split(" ") if w])
    disp = disp.title() if disp else ""

    return key, disp


def extract_rows(img_roi: np.ndarray) -> list[np.ndarray]:
    """Split battlelist ROI into row images.

    MVP strategy:
    - Slice ROI into N equal-height rows.
    - N defaults to 10 (common battlelist visible rows), configurable via env.

    This intentionally avoids complex separator detection for robustness.
    """

    if img_roi is None or not isinstance(img_roi, np.ndarray) or img_roi.size == 0:
        return []

    h = int(img_roi.shape[0])
    w = int(img_roi.shape[1])
    if h <= 2 or w <= 2:
        return []

    try:
        n_rows = int(float((os.getenv("BATTLELIST_N_ROWS", "10") or "10").strip()))
    except Exception:
        n_rows = 10
    n_rows = max(1, min(30, int(n_rows)))

    row_h = max(1, h // n_rows)
    rows: list[np.ndarray] = []
    for i in range(n_rows):
        y0 = i * row_h
        y1 = h if i == (n_rows - 1) else min(h, (i + 1) * row_h)
        if y1 <= y0:
            continue
        rows.append(img_roi[y0:y1, 0:w].copy())

    return rows


def ocr_name(row_img: np.ndarray) -> tuple[str, float]:
    """OCR battlelist row name.

    Returns (text, conf) where conf is best-effort in [0,1].
    """

    ocr = _get_ocr()
    if ocr is None:
        return "", 0.0

    try:
        processed = ocr.preprocess_image(row_img)
        # detail=1 yields: [ (bbox, text, conf), ... ]
        results = ocr.reader.readtext(processed, detail=1)
    except Exception:
        return "", 0.0

    best_text = ""
    best_conf = 0.0
    try:
        for item in results or []:
            try:
                _bbox, txt, conf = item
                t = str(txt or "").strip()
                c = float(conf or 0.0)
                if not t:
                    continue
                if c > best_conf:
                    best_conf = c
                    best_text = t
            except Exception:
                continue
    except Exception:
        pass

    return best_text, float(best_conf or 0.0)


def parse_row(row_img: np.ndarray, row_index: int) -> BattlelistEntry:
    text, conf = ocr_name(row_img)
    name_norm, display = _normalize_name(text)
    return {
        "row_index": int(row_index),
        "name_raw": str(text or ""),
        "name_norm": str(name_norm or ""),
        "name_display": str(display or ""),
        "conf": float(conf or 0.0),
    }


def stabilize(
    prev_buf: MutableMapping[int, List[BattlelistEntry]],
    entries: Iterable[BattlelistEntry],
    *,
    window_n: int = 10,
) -> list[BattlelistEntry]:
    """Stabilize entries per row_index using weighted majority over a sliding window.

    prev_buf:
      dict[row_index] -> list[entry] (kept to last N)

    Returns a list of stabilized entries (one per row_index present in entries).
    """

    window_n = max(1, int(window_n))

    # Update history buffer
    for e in entries:
        try:
            idx = int(e.get("row_index", 0))
        except Exception:
            idx = 0
        hist = prev_buf.get(idx)
        if hist is None:
            hist = []
            prev_buf[idx] = hist
        hist.append(dict(e))
        if len(hist) > window_n:
            prev_buf[idx] = hist[-window_n:]

    stabilized: list[BattlelistEntry] = []

    # Produce stabilized output for the rows present in *this* tick.
    for e in entries:
        try:
            idx = int(e.get("row_index", 0))
        except Exception:
            idx = 0
        hist = prev_buf.get(idx, [])
        if not hist:
            stabilized.append(dict(e))
            continue

        # Weighted vote by name_norm, weight=conf
        score_by_name: Dict[str, float] = defaultdict(float)
        last_seen_raw: Dict[str, str] = {}
        for he in hist:
            name_norm = str(he.get("name_norm", "") or "").strip().lower()
            if not name_norm:
                continue
            try:
                c = float(he.get("conf", 0.0) or 0.0)
            except Exception:
                c = 0.0
            score_by_name[name_norm] += max(0.0, c)
            try:
                last_seen_raw[name_norm] = str(he.get("name_raw", "") or "")
            except Exception:
                pass

        if not score_by_name:
            stabilized.append(dict(e))
            continue

        winner_norm = max(score_by_name.items(), key=lambda kv: kv[1])[0]
        total = sum(score_by_name.values())
        winner_score = float(score_by_name.get(winner_norm, 0.0) or 0.0)
        stable_conf = (winner_score / total) if total > 0 else 0.0

        # Canonicalize display
        _nn, display = _normalize_name(winner_norm)
        out = dict(e)
        out["name_norm"] = str(winner_norm)
        out["name_display"] = str(display or "")
        # Keep a stable raw name that is human-friendly.
        out["name_raw"] = str(display or last_seen_raw.get(winner_norm, "") or "")
        out["conf"] = float(stable_conf)
        stabilized.append(out)

    return stabilized
