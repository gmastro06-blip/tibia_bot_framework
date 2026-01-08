from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Tuple

import cv2


def _add_src_to_syspath() -> None:
    import sys

    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def load_roi_config(resolution: tuple[int, int]) -> tuple[dict, list[int]]:
    """Carga configuración de ROIs según la resolución detectada (helper de debug).

    Nota: el runtime usa `src/main.py:load_roi_config`. Este script mantiene su propio
    helper para no acoplar el bot con utilidades de debug.
    """
    import json

    width, height = resolution
    config_files = {
        (2048, 1076): "configs/rois_guess.json",
        (1920, 1080): "configs/rois_guess_1920x1080.json",
        (1920, 1009): "configs/rois_guess_1920x1080.json",
    }

    config_file = config_files.get((width, height), "configs/rois_guess_1920x1080.json")

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
            print(f"Configuración cargada desde {config_file} para resolución {width}x{height}")
            return config["rois_guess_norm"], config["source_resolution"]
    except FileNotFoundError:
        raise SystemExit(f"Archivo de configuración no encontrado: {config_file}")


def check_obs_websocket_status() -> bool:
    _add_src_to_syspath()
    from capture.obs_websocket_capture import OBSWebSocketCapture

    print("\n--- VERIFICACIÓN DE OBS WEBSOCKET ---")
    capture = OBSWebSocketCapture(capture_method="dxcam", source_name=os.getenv("OBS_SOURCE_NAME", "Tibia_Fuente"))

    if not capture.connect():
        print("❌ No se pudo conectar a OBS WebSocket")
        return False

    status = capture.get_obs_status()
    print(f"Estado OBS: {status}")
    print("✅ OBS WebSocket conectado correctamente")
    capture.disconnect()
    return True


def test_real_capture_debug() -> None:
    _add_src_to_syspath()

    from capture.obs_websocket_capture import OBSWebSocketCapture
    from gamestate.builder import GameStateBuilder

    print("\n--- PRUEBA CAPTURA REAL CON DEBUG ---")

    out_dir = Path("debug_images")
    out_dir.mkdir(parents=True, exist_ok=True)

    capture = OBSWebSocketCapture(capture_method="dxcam", source_name=os.getenv("OBS_SOURCE_NAME", "Tibia_Fuente"))
    print("Conectando a OBS via WebSocket...")

    if not capture.connect():
        raise SystemExit("❌ No se pudo conectar a OBS WebSocket")

    try:
        frame = capture.capture()
        if frame is None:
            raise SystemExit("❌ No se pudo capturar frame")

        print(f"✅ Captura exitosa: {frame.shape}")
        cv2.imwrite(str(out_dir / "captura_completa.png"), frame)

        resolution = (frame.shape[1], frame.shape[0])
        rois, source_resolution = load_roi_config(resolution)
        rois["_source_resolution"] = source_resolution

        def normalize_to_px(roi_norm: Dict[str, float]) -> Tuple[int, int, int, int]:
            frame_w, frame_h = resolution
            source_w, source_h = int(source_resolution[0]), int(source_resolution[1])

            scale = min(frame_w / source_w, frame_h / source_h) if source_w and source_h else 1.0
            content_w = source_w * scale
            content_h = source_h * scale
            offset_x = (frame_w - content_w) / 2.0
            offset_y = (frame_h - content_h) / 2.0

            x_src = roi_norm["x"] * source_w
            y_src = roi_norm["y"] * source_h
            w_src = roi_norm["w"] * source_w
            h_src = roi_norm["h"] * source_h

            x = int(round(offset_x + x_src * scale))
            y = int(round(offset_y + y_src * scale))
            w = int(round(w_src * scale))
            h = int(round(h_src * scale))

            x = max(0, min(x, frame_w - 1))
            y = max(0, min(y, frame_h - 1))
            w = max(1, min(w, frame_w - x))
            h = max(1, min(h, frame_h - y))
            return x, y, w, h

        overlay = frame.copy()
        for label, color in (("hp_top_ocr", (0, 255, 0)), ("mp_top_ocr", (255, 0, 0))):
            if label not in rois:
                continue
            x, y, w, h = normalize_to_px(rois[label])
            crop = frame[y : y + h, x : x + w]
            cv2.imwrite(str(out_dir / f"{label}.png"), crop)
            cv2.rectangle(overlay, (x, y), (x + w, y + h), color, 2)
            cv2.putText(overlay, label, (x, max(0, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        cv2.imwrite(str(out_dir / "captura_overlay_rois.png"), overlay)

        gs = GameStateBuilder().update_from_frame(frame, rois, resolution)
        print(f"HP: {gs.hp_current} | MP: {gs.mp_current}")
        print(f"Debug guardado en: {out_dir.resolve()}")
    finally:
        capture.disconnect()


if __name__ == "__main__":
    if not check_obs_websocket_status():
        raise SystemExit(1)
    test_real_capture_debug()
