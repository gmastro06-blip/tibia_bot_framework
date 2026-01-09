from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _pick_device() -> str | int:
    # Prefer CUDA only if torchvision NMS works on CUDA.
    forced = os.getenv("DEVICE", "").strip().lower()
    if forced:
        if forced in {"cpu"}:
            return "cpu"
        if forced in {"0", "cuda", "gpu"}:
            return 0
        return forced

    try:
        import torch

        if not torch.cuda.is_available():
            return "cpu"

        try:
            import torchvision

            boxes = torch.tensor([[0.0, 0.0, 10.0, 10.0]], device="cuda")
            scores = torch.tensor([0.9], device="cuda")
            _ = torchvision.ops.nms(boxes, scores, 0.5)
            return 0
        except Exception as e:
            print(f"CUDA available but torchvision NMS failed ({e}); using CPU for inference.")
            return "cpu"
    except Exception:
        return "cpu"


def main() -> None:
    _add_src_to_syspath()

    from capture.obs_websocket_capture import OBSWebSocketCapture
    from ultralytics import YOLO  # type: ignore[attr-defined]

    repo_root = Path(__file__).resolve().parents[1]

    weights = Path(os.getenv("WEIGHTS", "").strip() or (repo_root / "runs" / "tibia" / "creatures_v1_yolov82" / "weights" / "best.pt"))
    if not weights.exists():
        raise SystemExit(f"Weights not found: {weights}")

    host = os.getenv("OBS_HOST", "localhost")
    port = int(os.getenv("OBS_PORT", "4455"))
    password = os.getenv("OBS_PASSWORD", "")
    source_name = os.getenv("OBS_SOURCE_NAME", "Tibia_Fuente")
    capture_method = os.getenv("CAPTURE_METHOD", "dxcam")

    conf = float(os.getenv("CONF", "0.25"))

    device = _pick_device()
    print(f"Using weights: {weights}")
    print(f"Capture: method={capture_method} source={source_name} obs={host}:{port}")
    print(f"Inference device: {device}")
    print("Controls: 'q' quit | 's' save snapshot")

    model = YOLO(str(weights))

    cap = OBSWebSocketCapture(
        host=host,
        port=port,
        password=password,
        capture_method=capture_method,
        source_name=source_name,
    )

    if not cap.connect():
        raise SystemExit("Could not connect to OBS WebSocket/capture")

    out_dir = repo_root / "debug_images" / "live_detect"
    out_dir.mkdir(parents=True, exist_ok=True)

    headless = os.getenv("HEADLESS", "").strip().lower() in {"1", "true", "yes"}
    latest_path = out_dir / "latest.png"
    save_every_s = float(os.getenv("SAVE_EVERY_S", "0.5"))
    last_save_ts = 0.0

    # Verify if GUI display is available; if not, automatically fall back to headless.
    if not headless:
        try:
            cv2.namedWindow("Tibia Creatures (YOLOv8)", cv2.WINDOW_NORMAL)
        except cv2.error as e:
            print(f"OpenCV windowing not available ({e}); switching to headless mode.")
            headless = True

    last_t = time.time()
    fps = 0.0

    try:
        while True:
            frame = cap.capture()
            if frame is None:
                time.sleep(0.05)
                continue

            # Ultralytics expects RGB; it can handle BGR arrays but results.plot is BGR-friendly.
            results = model.predict(frame, conf=conf, device=device, verbose=False)
            r0 = results[0]
            vis = r0.plot()

            # Print a lightweight summary occasionally.
            if int(time.time()) % 2 == 0:
                try:
                    names = r0.names or {}
                    counts: dict[str, int] = {}
                    if r0.boxes is not None and len(r0.boxes) > 0:
                        for cls_id in r0.boxes.cls.tolist():
                            cls_id_i = int(cls_id)
                            label = str(names.get(cls_id_i, cls_id_i))
                            counts[label] = counts.get(label, 0) + 1
                    if counts:
                        print("detections:", counts)
                except Exception:
                    pass

            # FPS
            now = time.time()
            dt = max(1e-6, now - last_t)
            fps = 0.9 * fps + 0.1 * (1.0 / dt)
            last_t = now

            cv2.putText(
                vis,
                f"FPS: {fps:.1f}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            if headless:
                now_s = time.time()
                if now_s - last_save_ts >= save_every_s:
                    cv2.imwrite(str(latest_path), vis)
                    last_save_ts = now_s
            else:
                cv2.imshow("Tibia Creatures (YOLOv8)", vis)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("s"):
                    ts = time.time()
                    path = out_dir / f"snapshot_{ts:.3f}.png"
                    cv2.imwrite(str(path), vis)
                    print(f"Saved: {path}")

    finally:
        try:
            cap.disconnect()
        except Exception:
            pass
        if not headless:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass


if __name__ == "__main__":
    main()
