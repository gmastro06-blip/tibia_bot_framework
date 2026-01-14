from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Mapping, Optional

import numpy as np


# Public API
# - Returns a dict of best-effort confidences in [0, 1].
# - Empty dict means "no detection possible" (e.g., no templates configured).

def detect_status_icons(states_roi_img: np.ndarray) -> Dict[str, float]:
    """Detect status icons inside the `states_icons` ROI.

    Minimal acceptable method: template matching (normalized cross-correlation).

    Templates are loaded from disk (opt-in):
    - Directory: STATUS_ICONS_TEMPLATES_DIR (default: configs/status_icons)
    - Supported formats: .npy (preferred, grayscale uint8) and .png (if OpenCV is available)

    Expected template filenames (case-insensitive):
    - paralyzed.npy / paralyzed.png
    - haste_active.npy / haste_active.png (also supports haste.npy)
    - utamo_active.npy / utamo_active.png (also supports utamo.npy)

    Returns:
      dict{status_name: confidence}
    """

    if states_roi_img is None or not isinstance(states_roi_img, np.ndarray) or states_roi_img.size == 0:
        return {}

    templates = _load_templates_from_env()
    if not templates:
        return {}

    try:
        img = _to_gray(states_roi_img)
    except Exception:
        return {}

    try:
        stride = int(float(os.getenv("STATUS_ICON_STRIDE", "2").strip() or "2"))
    except Exception:
        stride = 2
    stride = max(1, min(8, int(stride)))

    out: Dict[str, float] = {}
    for name, tmpl in templates.items():
        try:
            s = _ncc_best(img, tmpl, stride=stride)
            out[str(name)] = float(max(0.0, min(1.0, s)))
        except Exception:
            continue

    return out


# -----------------
# Template loading
# -----------------

_TEMPLATE_CACHE: Dict[str, Dict[str, np.ndarray]] = {}


def _default_templates_dir() -> Path:
    # src/vision/status_icons.py -> parents: vision (0), src (1), repo (2)
    return Path(__file__).resolve().parents[2] / "configs" / "status_icons"


def _load_templates_from_env() -> Dict[str, np.ndarray]:
    raw = (os.getenv("STATUS_ICONS_TEMPLATES_DIR", "") or "").strip()
    base = Path(raw) if raw else _default_templates_dir()
    key = str(base.resolve())

    cached = _TEMPLATE_CACHE.get(key)
    if cached is not None:
        return cached

    templates: Dict[str, np.ndarray] = {}
    try:
        if not base.exists() or not base.is_dir():
            _TEMPLATE_CACHE[key] = {}
            return {}

        for p in base.iterdir():
            if not p.is_file():
                continue
            name = _normalize_template_name(p.stem)
            if not name:
                continue

            arr = _load_template_file(p)
            if arr is None:
                continue

            g = _to_gray(arr)
            if g.size == 0:
                continue
            # Ensure uint8
            if g.dtype != np.uint8:
                try:
                    g = g.astype(np.uint8)
                except Exception:
                    continue

            templates[name] = g
    except Exception:
        templates = {}

    _TEMPLATE_CACHE[key] = templates
    return templates


def _normalize_template_name(stem: str) -> str:
    s = (stem or "").strip().lower()
    if not s:
        return ""

    # Allow synonyms in filenames.
    if s in {"paralyze", "paralize", "paralyzed", "paralysis"}:
        return "paralyzed"
    if s in {"haste", "haste_active"}:
        return "haste_active"
    if s in {"utamo", "utamo_active", "utamo_vita"}:
        return "utamo_active"

    # Ignore unrelated templates.
    if s not in {"paralyzed", "haste_active", "utamo_active"}:
        return ""

    return s


def _load_template_file(p: Path) -> Optional[np.ndarray]:
    suf = p.suffix.lower()
    if suf == ".npy":
        try:
            arr = np.load(str(p))
            if isinstance(arr, np.ndarray):
                return arr
        except Exception:
            return None
        return None

    if suf == ".png":
        try:
            import cv2  # type: ignore

            img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
            if img is None:
                return None
            return img
        except Exception:
            return None

    return None


# -----------------
# Matching
# -----------------


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.ndim == 3 and img.shape[2] >= 3:
        b = img[:, :, 0].astype(np.float32)
        g = img[:, :, 1].astype(np.float32)
        r = img[:, :, 2].astype(np.float32)
        gray = (0.114 * b + 0.587 * g + 0.299 * r).astype(np.uint8)
        return gray
    # Fallback
    return img.reshape(-1).astype(np.uint8)


def _ncc_best(img_gray: np.ndarray, tmpl_gray: np.ndarray, *, stride: int = 2) -> float:
    """Return best normalized cross-correlation score in [-1, 1]."""

    if img_gray is None or tmpl_gray is None:
        return 0.0

    ih, iw = int(img_gray.shape[0]), int(img_gray.shape[1])
    th, tw = int(tmpl_gray.shape[0]), int(tmpl_gray.shape[1])

    if th <= 0 or tw <= 0:
        return 0.0
    if ih < th or iw < tw:
        return 0.0

    # Convert to float once
    tmpl = tmpl_gray.astype(np.float32)
    tmpl = tmpl - float(tmpl.mean())
    tmpl_norm = float(np.linalg.norm(tmpl))
    if tmpl_norm <= 1e-6:
        return 0.0

    img_f = img_gray.astype(np.float32)

    best = -1.0
    for y in range(0, ih - th + 1, int(stride)):
        for x in range(0, iw - tw + 1, int(stride)):
            patch = img_f[y : y + th, x : x + tw]
            patch = patch - float(patch.mean())
            denom = float(np.linalg.norm(patch)) * tmpl_norm
            if denom <= 1e-6:
                continue
            score = float((patch * tmpl).sum()) / denom
            if score > best:
                best = score

    return float(best)
