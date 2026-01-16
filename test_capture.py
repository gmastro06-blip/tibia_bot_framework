from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Tuple

import cv2


repo_root = Path(__file__).resolve().parent
src_dir = repo_root / "src"
sys.path.insert(0, str(src_dir))

from capture.dxgi_capture import DXGICapture
from vision.ocr import OCRProcessor


def load_roi_config(resolution: Tuple[int, int]) -> tuple[dict, list[int]]:
    width, height = resolution
    config_files = {
        (2048, 1076): "configs/rois_guess.json",
        (1920, 1080): "configs/rois_guess_1920x1080.json",
        (1920, 1009): "configs/rois_guess_1920x1080.json",
    }

    config_file = config_files.get((width, height), "configs/rois_guess_1920x1080.json")
    with open(config_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"Configuración cargada desde {config_file} para resolución {width}x{height}")
    return data["rois_guess_norm"], data["source_resolution"]


def draw_roi(img: Any, name: str, roi: Tuple[int, int, int, int], color: Tuple[int, int, int]) -> None:
    x, y, w, h = roi
    cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
    cv2.putText(img, name, (x, max(0, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)


def main() -> None:
    force_monitor_raw = os.getenv("FORCE_MONITOR", "").strip()
    force_monitor = None
    if force_monitor_raw:
        try:
            force_monitor = int(force_monitor_raw)
        except Exception:
            force_monitor = None

    out_dir = Path("debug_images_real")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = f"{time.time():.6f}".replace(".", "_")

    capture = DXGICapture(force_monitor=force_monitor)
    frame = None
    for _ in range(50):
        frame = capture.capture()
        if frame is not None:
            break
        time.sleep(0.05)

    if frame is None:
        raise SystemExit("No se pudo capturar ningún frame")

    resolution = (int(frame.shape[1]), int(frame.shape[0]))
    rois, source_resolution = load_roi_config(resolution)
    rois["_source_resolution"] = source_resolution

    # Guardar frame base
    m_tag = "auto" if force_monitor is None else f"m{force_monitor}"
    full_path = out_dir / f"dxgi_full_{ts}_{m_tag}_{resolution[0]}x{resolution[1]}.png"
    cv2.imwrite(str(full_path), frame)
    print(f"Guardado: {full_path}")

    # Usar exactamente el mismo normalizador del OCR (soporta unit=px/norm + letterboxing)
    ocr = OCRProcessor()
    overlay = frame.copy()

    # Colores por grupo
    colors = {
        "hp_top_ocr": (0, 255, 0),
        "mp_top_ocr": (255, 0, 0),
        "hpmp_top_strip": (0, 255, 255),
        "hp_low_bar": (0, 200, 0),
        "mp_low_bar": (200, 0, 0),
    }

    # Dibujar todas las ROIs (las principales con color fijo; el resto en verde tenue)
    for name, roi_def in rois.items():
        if name.startswith("_"):
            continue
        roi_px = ocr._roi_to_px(frame, rois, resolution, roi_def)
        color = colors.get(name, (120, 255, 120))
        draw_roi(overlay, name, roi_px, color)

    overlay_path = out_dir / f"dxgi_overlay_{ts}_{m_tag}_{resolution[0]}x{resolution[1]}.png"
    cv2.imwrite(str(overlay_path), overlay)
    print(f"Guardado: {overlay_path}")

    # Guardar crops clave
    for name in ["hp_top_ocr", "mp_top_ocr", "hpmp_top_strip", "hp_low_bar", "mp_low_bar"]:
        if name not in rois:
            continue
        x, y, w, h = ocr._roi_to_px(frame, rois, resolution, rois[name])
        crop = frame[y : y + h, x : x + w]
        crop_path = out_dir / f"dxgi_crop_{name}_{ts}_{m_tag}.png"
        cv2.imwrite(str(crop_path), crop)
        print(f"Guardado: {crop_path} ({w}x{h})")


if __name__ == "__main__":
    main()