from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _load_roi_config(resolution: tuple[int, int]) -> tuple[dict, list[int], str]:
    width, height = resolution
    config_files = {
        (2048, 1076): "configs/rois_guess.json",
        (1920, 1080): "configs/rois_guess_1920x1080.json",
        (1920, 1009): "configs/rois_guess_1920x1080.json",
    }
    config_file = config_files.get((width, height), "configs/rois_guess_1920x1080.json")
    with open(config_file, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    rois_any = cfg.get("rois_guess_norm", {})
    if not isinstance(rois_any, dict):
        rois_any = {}

    src_any = cfg.get("source_resolution", [0, 0])
    src_list: list[int] = [0, 0]
    try:
        if isinstance(src_any, (list, tuple)) and len(src_any) >= 2:
            src_list = [int(src_any[0]), int(src_any[1])]
    except Exception:
        src_list = [0, 0]

    return rois_any, src_list, str(config_file)


def _compute_letterbox(frame_w: int, frame_h: int, source_w: int, source_h: int) -> tuple[float, float, float]:
    scale = min(frame_w / source_w, frame_h / source_h) if source_w and source_h else 1.0
    content_w = source_w * scale
    content_h = source_h * scale
    offset_x = (frame_w - content_w) / 2.0
    offset_y = (frame_h - content_h) / 2.0
    return scale, offset_x, offset_y


def _frame_px_to_source_norm(
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    frame_w: int,
    frame_h: int,
    source_w: int,
    source_h: int,
) -> dict:
    scale, offset_x, offset_y = _compute_letterbox(frame_w, frame_h, source_w, source_h)
    if scale <= 0:
        scale = 1.0

    sx = (float(x) - offset_x) / scale
    sy = (float(y) - offset_y) / scale
    sw = float(w) / scale
    sh = float(h) / scale

    sx = max(0.0, min(float(source_w - 1), sx))
    sy = max(0.0, min(float(source_h - 1), sy))
    sw = max(1.0, min(float(source_w) - sx, sw))
    sh = max(1.0, min(float(source_h) - sy, sh))

    return {
        "x": float(sx) / float(source_w),
        "y": float(sy) / float(source_h),
        "w": float(sw) / float(source_w),
        "h": float(sh) / float(source_h),
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live smoke test: read CAP (capacity) via OCR.")
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--fps", type=float, default=8.0)
    p.add_argument("--threshold", type=int, default=int(float(os.getenv("CAP_LEAVE_THRESHOLD", "50") or 50)))
    p.add_argument("--debug", action="store_true", help="Print extra OCR debug and attempt ROI auto-suggest.")
    p.add_argument(
        "--autowrite",
        action="store_true",
        help="If debug suggests a cap ROI, write it as cap_ocr into the active config automatically.",
    )
    p.add_argument(
        "--save-dir",
        type=str,
        default=str(Path("logs") / "debug_cap"),
        help="Directory where debug crops/snapshots are saved.",
    )
    return p.parse_args()


def main() -> int:
    _add_src_to_syspath()

    from capture.dxgi_capture import DXGICapture
    from vision.ocr import OCRProcessor

    args = _parse_args()

    cap = DXGICapture(force_monitor=args.monitor)
    ocr = OCRProcessor()

    save_dir = Path(args.save_dir)
    try:
        save_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    t_end = time.time() + float(args.seconds)
    period = 1.0 / max(1.0, float(args.fps))

    rois: dict | None = None
    resolution: tuple[int, int] | None = None
    cfg_path: str | None = None
    source_resolution: list[int] | None = None

    last_print = 0.0
    print(f"▶ Smoke live CAP. monitor={args.monitor} seconds={args.seconds} fps={args.fps} threshold={args.threshold}")
    print(f"   debug={bool(args.debug)} save_dir={save_dir}")

    def _suggest_cap_roi_from_skills_panel(frame, rois, resolution) -> tuple[dict[str, Any], int | None]:
        """Try to locate Cap value bbox inside skills_panel via EasyOCR.

        Returns (suggested_roi_def_px, cap_value) if found, otherwise ({}, None).
        """
        try:
            if rois.get("skills_panel") is None:
                return {}, None
            x, y, w, h = ocr._roi_to_px(frame, rois, resolution, rois["skills_panel"])  # type: ignore[arg-type]
            crop = frame[y : y + h, x : x + w]
            processed = ocr.preprocess_image(crop)
            # detail=1: [ (bbox, text, conf), ... ]
            results = ocr.reader.readtext(processed, detail=1, allowlist=None)
            if not results:
                return {}, None

            # Normalize entries
            entries = []
            for bbox, text, conf in results:
                try:
                    s = str(text or "")
                    if not s:
                        continue
                    # bbox is 4 points [[x,y],...]
                    xs = [p[0] for p in bbox]
                    ys = [p[1] for p in bbox]
                    x0, y0, x1, y1 = float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))
                    entries.append((s, float(conf or 0.0), x0, y0, x1, y1))
                except Exception:
                    continue

            cap_labels = [e for e in entries if "cap" in e[0].lower()]
            if not cap_labels:
                return {}, None

            # Pick most confident cap label
            cap_label = max(cap_labels, key=lambda e: e[1])
            _s, _c, lx0, ly0, lx1, ly1 = cap_label
            ly_mid = (ly0 + ly1) / 2.0

            # Find numeric-looking entries on the same line, to the right
            nums = []
            for s, conf, x0, y0, x1, y1 in entries:
                if x0 <= lx1:
                    continue
                y_mid = (y0 + y1) / 2.0
                if abs(y_mid - ly_mid) > max(10.0, (ly1 - ly0) * 1.5):
                    continue
                m = [int(n) for n in __import__("re").findall(r"\d{1,6}", s)]
                if not m:
                    continue
                nums.append((min(m), conf, x0, y0, x1, y1))

            if not nums:
                return {}, None

            # closest to label (smallest x0)
            val, _conf, nx0, ny0, nx1, ny1 = min(nums, key=lambda t: t[2])

            # bbox in processed image coordinates; map back 1:1 to crop (same size)
            pad = 4
            rx0 = max(0, int(nx0) - pad)
            ry0 = max(0, int(ny0) - pad)
            rx1 = min(int(processed.shape[1]), int(nx1) + pad)
            ry1 = min(int(processed.shape[0]), int(ny1) + pad)

            # processed is 2x scaled inside OCRProcessor.preprocess_image(); so map to crop coords /2
            # NOTE: preprocess_image scales by 2x unconditionally.
            sx0 = int(round(rx0 / 2))
            sy0 = int(round(ry0 / 2))
            sx1 = int(round(rx1 / 2))
            sy1 = int(round(ry1 / 2))

            roi_px = {
                "unit": "px",
                "x": int(x + sx0),
                "y": int(y + sy0),
                "w": int(max(1, sx1 - sx0)),
                "h": int(max(1, sy1 - sy0)),
            }
            return roi_px, int(val)
        except Exception:
            return {}, None

    try:
        while time.time() < t_end:
            t0 = time.time()
            frame = cap.capture()
            if frame is None:
                time.sleep(0.05)
                continue

            if rois is None or resolution is None:
                resolution = (int(frame.shape[1]), int(frame.shape[0]))
                rois_loaded, source_resolution_loaded, cfg_path_loaded = _load_roi_config(resolution)
                rois_loaded["_source_resolution"] = source_resolution_loaded
                rois = rois_loaded
                source_resolution = list(source_resolution_loaded)
                cfg_path = str(cfg_path_loaded)
                print(f"🧭 ROIs loaded for {resolution[0]}x{resolution[1]} (source={source_resolution})")
                if rois is not None and "cap_ocr" in rois:
                    print("✅ ROI cap_ocr detectada en config")
                else:
                    print("ℹ️  ROI cap_ocr NO existe; usando fallback parse en skills_panel")

            cap_cur = None
            try:
                cap_cur = ocr.extract_capacity(frame, rois, resolution)  # type: ignore[arg-type]
            except Exception:
                cap_cur = None

            if args.debug and rois is not None and "cap_ocr" in rois:
                try:
                    import cv2

                    x, y, w, h = ocr._roi_to_px(frame, rois, resolution, rois["cap_ocr"])  # type: ignore[arg-type]
                    crop = frame[y : y + h, x : x + w]
                    cv2.imwrite(str(save_dir / f"{time.time():.6f}_cap_ocr.png"), crop)
                    processed = ocr.preprocess_image(crop)
                    cv2.imwrite(str(save_dir / f"{time.time():.6f}_cap_ocr_processed.png"), processed)
                    try:
                        raw0 = ocr.reader.readtext(processed, detail=0, allowlist="0123456789")
                        raw1 = ocr.reader.readtext(processed, detail=1, allowlist="0123456789")
                        print(f"🔎 cap_ocr raw detail=0: {raw0}")
                        if raw1:
                            # show top few
                            print(f"🔎 cap_ocr raw detail=1 (top): {raw1[:3]}")
                    except Exception as e:
                        print(f"🔎 cap_ocr readtext failed: {e}")
                except Exception as e:
                    print(f"🔎 cap_ocr debug crop failed: {e}")

            low_cap = None
            try:
                if cap_cur is not None:
                    low_cap = bool(int(cap_cur) <= int(args.threshold))
            except Exception:
                low_cap = None

            reco = "leave depot" if low_cap else ""

            now = time.time()
            if now - last_print >= 0.5:
                last_print = now
                cap_str = str(cap_cur) if cap_cur is not None else "?"
                low_str = "?" if low_cap is None else ("YES" if low_cap else "no")
                print(f"CAP {cap_str} | low_cap={low_str} | reco={reco or '-'}")

                if args.debug and cap_cur is None and rois is not None:
                    roi_px, guessed = _suggest_cap_roi_from_skills_panel(frame, rois, resolution)
                    if roi_px:
                        print(f"🧩 Suggested cap_ocr ROI (px): {roi_px} guessed={guessed}")
                        # Save a snapshot crop for manual verification
                        try:
                            import cv2

                            x = int(roi_px["x"])
                            y = int(roi_px["y"])
                            w = int(roi_px["w"])
                            h = int(roi_px["h"])
                            crop = frame[y : y + h, x : x + w]
                            cv2.imwrite(str(save_dir / f"{now:.6f}_cap_ocr_suggest.png"), crop)
                        except Exception:
                            pass

                        if args.autowrite and guessed is not None and cfg_path and source_resolution:
                            try:
                                frame_w = int(frame.shape[1])
                                frame_h = int(frame.shape[0])
                                source_w = int(source_resolution[0])
                                source_h = int(source_resolution[1])
                                roi_norm = _frame_px_to_source_norm(
                                    x=int(roi_px.get("x", 0)),
                                    y=int(roi_px.get("y", 0)),
                                    w=int(roi_px.get("w", 0)),
                                    h=int(roi_px.get("h", 0)),
                                    frame_w=frame_w,
                                    frame_h=frame_h,
                                    source_w=source_w,
                                    source_h=source_h,
                                )

                                # Write into the config file.
                                with open(cfg_path, "r", encoding="utf-8") as f:
                                    cfg = json.load(f)
                                cfg.setdefault("rois_guess_norm", {})
                                cfg["rois_guess_norm"]["cap_ocr"] = roi_norm
                                with open(cfg_path, "w", encoding="utf-8") as f:
                                    json.dump(cfg, f, ensure_ascii=False, indent=2)

                                # Update in-memory rois and try again immediately.
                                rois["cap_ocr"] = roi_norm
                                cap_try = None
                                try:
                                    cap_try = ocr.extract_capacity(frame, rois, resolution)  # type: ignore[arg-type]
                                except Exception:
                                    cap_try = None
                                print(f"✍️  Autowrote cap_ocr into {cfg_path}. Immediate read: {cap_try}")
                            except Exception as e:
                                print(f"⚠️  Autowrite failed: {e}")

            elapsed = time.time() - t0
            to_sleep = max(0.0, period - elapsed)
            if to_sleep:
                time.sleep(to_sleep)
    except KeyboardInterrupt:
        print("\n🛑 Interrupted")

    print("✅ Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
