from __future__ import annotations

import os
import time
from pathlib import Path

import cv2
import numpy as np
from mss import mss


def main() -> None:
    out_dir = Path(os.getenv("CAPTURE_DEBUG_DIR", "debug_images_real"))
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = f"{time.time():.6f}".replace(".", "_")

    with mss() as sct:
        print(f"Monitores disponibles (incluye 0 combinado): {len(sct.monitors)}")
        for i, mon in enumerate(sct.monitors):
            left, top, width, height = mon["left"], mon["top"], mon["width"], mon["height"]
            print(f"  Monitor {i}: pos=({left},{top}) size={width}x{height}")

        for i, mon in enumerate(sct.monitors):
            try:
                shot = sct.grab(mon)
                frame = np.frombuffer(shot.bgra, dtype=np.uint8).reshape((shot.height, shot.width, 4))
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                mean = float(frame_bgr.mean())
                std = [float(frame_bgr[:, :, c].std()) for c in range(3)]

                name = f"mon_{i}_{mon['width']}x{mon['height']}_{ts}.png"
                path = out_dir / name
                cv2.imwrite(str(path), frame_bgr)
                print(f"Guardado {path} | mean={mean:.2f} std={std}")
            except Exception as e:
                print(f"Error capturando monitor {i}: {e}")


if __name__ == "__main__":
    main()
