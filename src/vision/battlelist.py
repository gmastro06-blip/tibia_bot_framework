from __future__ import annotations

from collections import defaultdict
import os
from pathlib import Path
import time
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

import numpy as np

from vision.ocr import OCRProcessor


# Public types (dict-based to keep it simple and JSONL-friendly)
BattlelistEntry = Dict[str, Any]


_OCR_SINGLETON: OCRProcessor | None = None
_OCR_CORRECTIONS: Dict[str, str] | None = None

# Debug throttling (battlelist OCR can be called per-row per-frame).
_DEBUG_LAST_DUMP_TS: float = 0.0


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


def _env_float(name: str, default: float) -> float:
    try:
        raw = (os.getenv(name, str(default)) or str(default)).strip()
        return float(raw)
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = (os.getenv(name, "") or "").strip().lower()
        if raw == "":
            return bool(default)
        return raw in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _crop_text_region(row_img: np.ndarray) -> np.ndarray:
    """Crop row image to likely text area.

    Battlelist rows usually contain left-side icons/bars and a right-side scrollbar.
    Cropping improves OCR success and reduces false detections.

    Tunables (percentages of width):
      - BATTLELIST_ROW_CROP_LEFT_PCT (default 0.12)
      - BATTLELIST_ROW_CROP_RIGHT_PCT (default 0.98)
    """

    if row_img is None or not isinstance(row_img, np.ndarray) or row_img.size == 0:
        return row_img

    try:
        w = int(row_img.shape[1])
    except Exception:
        return row_img

    if w <= 4:
        return row_img

    left = _env_float("BATTLELIST_ROW_CROP_LEFT_PCT", 0.12)
    right = _env_float("BATTLELIST_ROW_CROP_RIGHT_PCT", 0.98)
    left = max(0.0, min(0.95, float(left)))
    right = max(left + 0.01, min(1.0, float(right)))

    x0 = int(max(0, min(w - 2, int(round(w * left)))))
    x1 = int(max(x0 + 1, min(w, int(round(w * right)))))
    try:
        return row_img[:, x0:x1].copy()
    except Exception:
        return row_img


def _readtext_best_effort(ocr: OCRProcessor, img: np.ndarray) -> list:
    """Call EasyOCR readtext with tuned parameters (best-effort across versions)."""

    # Conservative defaults; tweak via env if needed.
    text_threshold = _env_float("BATTLELIST_TEXT_THRESHOLD", 0.30)
    low_text = _env_float("BATTLELIST_LOW_TEXT", 0.20)
    link_threshold = _env_float("BATTLELIST_LINK_THRESHOLD", 0.20)
    mag_ratio = _env_float("BATTLELIST_MAG_RATIO", 1.0)

    # Some easyocr versions reject unknown kwargs; we retry with less.
    kwargs = {
        "detail": 1,
        "paragraph": False,
        "text_threshold": float(text_threshold),
        "low_text": float(low_text),
        "link_threshold": float(link_threshold),
        "mag_ratio": float(max(0.5, min(6.0, mag_ratio))),
    }

    try:
        return ocr.reader.readtext(img, **kwargs)
    except TypeError:
        # Retry with only standard args.
        try:
            return ocr.reader.readtext(img, detail=1)
        except Exception:
            return []
    except Exception:
        return []


def _best_from_results(results: list) -> tuple[str, float]:
    best_text = ""
    best_conf = 0.0
    for item in results or []:
        try:
            _bbox, txt, conf = item
            t = str(txt or "").strip()
            if not t:
                continue
            c = float(conf or 0.0)
            # Tie-break: prefer longer strings when confidence is close.
            if (c > best_conf) or (abs(c - best_conf) <= 1e-6 and len(t) > len(best_text)):
                best_conf = c
                best_text = t
        except Exception:
            continue
    return best_text, float(best_conf or 0.0)


def _alt_preprocess(row_img: np.ndarray, *, invert: bool) -> np.ndarray | None:
    try:
        import cv2

        if row_img is None or not isinstance(row_img, np.ndarray) or row_img.size == 0:
            return None

        if row_img.ndim == 3:
            gray = cv2.cvtColor(row_img, cv2.COLOR_BGR2GRAY)
        else:
            gray = row_img

        # Upscale aggressively for small fonts.
        scale = _env_float("BATTLELIST_UPSCALE", 4.0)
        scale = max(1.0, min(8.0, float(scale)))
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        # Contrast + threshold.
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        th = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            5,
        )
        if invert:
            th = 255 - th

        # Small dilation to connect letters.
        k = np.ones((2, 2), np.uint8)
        th = cv2.dilate(th, k, iterations=1)
        return th
    except Exception:
        return None


def _maybe_dump_debug(*, row_index: int, raw: np.ndarray, variants: list[tuple[str, np.ndarray]]) -> None:
    global _DEBUG_LAST_DUMP_TS

    if not _env_bool("BATTLELIST_DEBUG", False):
        return

    # Only dump for first row to keep it cheap and readable.
    if int(row_index) != 0:
        return

    interval_s = _env_float("BATTLELIST_DEBUG_INTERVAL_S", 2.0)
    interval_s = max(0.2, float(interval_s))
    now = time.time()
    if (now - float(_DEBUG_LAST_DUMP_TS or 0.0)) < interval_s:
        return

    _DEBUG_LAST_DUMP_TS = float(now)

    out_dir = Path((os.getenv("BATTLELIST_DEBUG_DIR", "logs/battlelist_debug") or "logs/battlelist_debug").strip())
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    try:
        import cv2

        ts = f"{now:.6f}"
        cv2.imwrite(str(out_dir / f"{ts}_row{int(row_index)}_raw.png"), raw)
        for name, img in variants:
            try:
                cv2.imwrite(str(out_dir / f"{ts}_row{int(row_index)}_{name}.png"), img)
            except Exception:
                continue
    except Exception:
        return


def ocr_name(row_img: np.ndarray, *, row_index: int = 0) -> tuple[str, float]:
    """OCR battlelist row name.

    Returns (text, conf) where conf is best-effort in [0,1].
    """

    ocr = _get_ocr()
    if ocr is None:
        return "", 0.0

    processed = None
    alt = None
    alt_inv = None
    try:
        processed = ocr.preprocess_image(row_img)
    except Exception:
        processed = None
    try:
        alt = _alt_preprocess(row_img, invert=False)
        alt_inv = _alt_preprocess(row_img, invert=True)
    except Exception:
        alt = None
        alt_inv = None

    variants: list[tuple[str, np.ndarray]] = []
    if isinstance(processed, np.ndarray) and processed.size:
        variants.append(("pre", processed))
    # Also try raw (sometimes preprocessing hurts).
    if isinstance(row_img, np.ndarray) and row_img.size:
        variants.append(("raw", row_img))
    if isinstance(alt, np.ndarray) and alt.size:
        variants.append(("alt", alt))
    if isinstance(alt_inv, np.ndarray) and alt_inv.size:
        variants.append(("alt_inv", alt_inv))

    # Optional debug dumps (throttled)
    try:
        _maybe_dump_debug(row_index=int(row_index), raw=row_img, variants=variants)
    except Exception:
        pass

    best_text = ""
    best_conf = 0.0
    for _name, img in variants:
        try:
            results = _readtext_best_effort(ocr, img)
            t, c = _best_from_results(results)
            if (c > best_conf) or (abs(c - best_conf) <= 1e-6 and len(t) > len(best_text)):
                best_conf = float(c)
                best_text = str(t)
        except Exception:
            continue

    return str(best_text or ""), float(best_conf or 0.0)


def parse_row(row_img: np.ndarray, row_index: int) -> BattlelistEntry:
    # Crop to the text region (skip icons/bars and scrollbar).
    row_for_ocr = row_img
    try:
        row_for_ocr = _crop_text_region(row_img)
    except Exception:
        row_for_ocr = row_img

    text, conf = ocr_name(row_for_ocr, row_index=int(row_index))
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
