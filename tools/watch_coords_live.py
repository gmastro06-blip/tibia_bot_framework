from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _resolve_rois_path(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        p = Path(s)
        if not p.is_absolute():
            repo_root = Path(__file__).resolve().parent.parent
            p = (repo_root / p).resolve()
        if p.exists() and p.is_file():
            return str(p)
    except Exception:
        return None
    return None


def _pick_config_file(resolution: tuple[int, int]) -> str:
    width, height = resolution
    config_files = {
        (2048, 1076): "configs/rois_guess.json",
        (1920, 1080): "configs/rois_guess_1920x1080.json",
        (1920, 1009): "configs/rois_guess_1920x1080.json",
    }
    return config_files.get((width, height), "configs/rois_guess_1920x1080.json")


def _load_config(path: str) -> tuple[dict[str, Any], list[int]]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    rois = cfg.get("rois_guess_norm", cfg.get("rois", cfg))
    source_resolution = cfg.get("source_resolution", [0, 0])
    if not isinstance(rois, dict):
        rois = {}
    return rois, source_resolution


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live watch of OCR coords (no inputs).")
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--interval-ms", type=int, default=200)
    p.add_argument(
        "--warmup-s",
        type=float,
        default=5.0,
        help="Seconds to wait before starting sampling (gives time to focus the game/move).",
    )
    p.add_argument(
        "--beep-on-change",
        action="store_true",
        help="Play a short beep when coords change (Windows only).",
    )
    p.add_argument(
        "--rois",
        type=str,
        default="",
        help="Path to ROI config JSON. If omitted, uses ROIS_CONFIG env var, else auto-picks by resolution.",
    )
    p.add_argument(
        "--out-jsonl",
        type=str,
        default="",
        help="Optional JSONL output path (events).",
    )
    p.add_argument(
        "--save-crops",
        action="store_true",
        help="Save coords_ocr crop (and expanded crop) on first sample and on changes.",
    )
    p.add_argument(
        "--crops-dir",
        type=str,
        default=str(Path("logs") / "coords_watch_crops"),
        help="Directory to write crops when --save-crops is enabled.",
    )
    return p.parse_args()


def _crop(img, rect: tuple[int, int, int, int]):
    x, y, w, h = rect
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(int(img.shape[1]), x0 + max(1, int(w)))
    y1 = min(int(img.shape[0]), y0 + max(1, int(h)))
    return img[y0:y1, x0:x1].copy()


def _expand_rect(img, rect: tuple[int, int, int, int], *, pad_x_frac: float, pad_y_frac: float) -> tuple[int, int, int, int]:
    x, y, w, h = rect
    x0 = max(0, int(x - (w * pad_x_frac)))
    y0 = max(0, int(y - (h * pad_y_frac)))
    x1 = min(int(img.shape[1]), int(x + w + (w * pad_x_frac)))
    y1 = min(int(img.shape[0]), int(y + h + (h * pad_y_frac)))
    return x0, y0, max(1, int(x1 - x0)), max(1, int(y1 - y0))


def _jsonl_append(path: str, event: dict[str, Any]) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main() -> int:
    _add_src_to_syspath()

    from capture.dxgi_capture import DXGICapture
    from vision.ocr import OCRProcessor
    import cv2

    args = _parse_args()

    cap = DXGICapture(force_monitor=int(args.monitor))
    ocr = OCRProcessor()

    # Grab one real frame to select config.
    frame = None
    for _ in range(80):
        frame = cap.capture()
        if frame is not None:
            break
        time.sleep(0.05)

    if frame is None:
        print("No pude capturar ningún frame (frame=None). Revisa FORCE_MONITOR/ventana visible.")
        return 2

    resolution = (int(frame.shape[1]), int(frame.shape[0]))

    rois_path = _resolve_rois_path(args.rois) or _resolve_rois_path(os.getenv("ROIS_CONFIG", ""))
    if not rois_path:
        rois_path = _pick_config_file(resolution)

    rois, source_resolution = _load_config(rois_path)

    if "coords_ocr" not in rois:
        print(f"El config {rois_path} no tiene ROI 'coords_ocr'.")
        return 3

    print(f"monitor={args.monitor} resolution={resolution[0]}x{resolution[1]} config={rois_path} source={source_resolution}")
    print(f"Warmup: {max(0.0, float(args.warmup_s)):.1f}s")
    print("Mueve el personaje ahora (1–2 tiles). Ctrl+C para cortar.")

    warmup_s = max(0.0, float(args.warmup_s))
    if warmup_s:
        try:
            end = time.time() + warmup_s
            while True:
                left = end - time.time()
                if left <= 0:
                    break
                # simple countdown (rate-limited)
                print(f"starting in {left:.1f}s...", end="\r")
                time.sleep(min(0.25, max(0.0, left)))
            print("starting now...     ")
        except KeyboardInterrupt:
            print("\ninterrupted.")
            return 130

    # Audible cue: sampling start.
    if args.beep_on_change:
        try:
            import winsound

            winsound.Beep(660, 120)
            winsound.Beep(880, 120)
        except Exception:
            try:
                print("\a", end="")
            except Exception:
                pass

    t_end = time.time() + max(1.0, float(args.seconds))
    interval_s = max(0.05, float(args.interval_ms) / 1000.0)

    last = None
    ok = 0
    total = 0
    changes = 0

    # Precompute pixel rect once (same normalization used by OCRProcessor).
    coords_rect_px: tuple[int, int, int, int] | None = None
    try:
        if isinstance(rois.get("coords_ocr"), Mapping):
            coords_rect_px = ocr._roi_to_px(frame, rois, resolution, rois["coords_ocr"])  # type: ignore[attr-defined]
    except Exception:
        coords_rect_px = None

    crops_base: Path | None = None
    if args.save_crops and coords_rect_px is not None:
        try:
            crops_base = Path(args.crops_dir) / f"{time.time():.6f}"
            crops_base.mkdir(parents=True, exist_ok=True)
            meta = {
                "monitor": int(args.monitor),
                "resolution": [int(resolution[0]), int(resolution[1])],
                "config": rois_path,
                "coords_ocr_px": [int(coords_rect_px[0]), int(coords_rect_px[1]), int(coords_rect_px[2]), int(coords_rect_px[3])],
            }
            (crops_base / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            crops_base = None

    idx = 0
    while time.time() < t_end:
        t0 = time.time()
        f = cap.capture()
        coords = None
        if f is not None:
            try:
                coords = ocr.extract_coords(f, rois, resolution)
            except Exception:
                coords = None

        total += 1
        if coords is not None:
            ok += 1

        changed = coords is not None and coords != last
        if changed:
            changes += 1
            dx = dy = dz = None
            try:
                if last is not None:
                    dx = int(coords[0]) - int(last[0])
                    dy = int(coords[1]) - int(last[1])
                    z0 = int(last[2]) if last[2] is not None else None
                    z1 = int(coords[2]) if coords[2] is not None else None
                    dz = (z1 - z0) if (z0 is not None and z1 is not None) else None
            except Exception:
                dx = dy = dz = None
            last = coords

        ts = time.time()
        line = f"{ts:.3f} coords={coords}"
        if changed:
            if dx is not None and dy is not None:
                line += f"  CHANGED dx={dx} dy={dy}" + (f" dz={dz}" if dz is not None else "")
            else:
                line += "  CHANGED"
        print(line)

        # Save crops for visual confirmation.
        if crops_base is not None and f is not None and coords_rect_px is not None:
            try:
                if idx == 0 or changed:
                    crop = _crop(f, coords_rect_px)
                    exp_rect = _expand_rect(f, coords_rect_px, pad_x_frac=0.25, pad_y_frac=0.50)
                    exp = _crop(f, exp_rect)
                    tag = f"{idx:04d}_{'changed' if changed else 'first'}"
                    cv2.imwrite(str(crops_base / f"{tag}_coords_ocr.png"), crop)
                    cv2.imwrite(str(crops_base / f"{tag}_coords_ocr_expanded.png"), exp)
            except Exception:
                pass

        if changed and args.beep_on_change:
            try:
                import winsound

                winsound.Beep(880, 120)
            except Exception:
                try:
                    print("\a", end="")
                except Exception:
                    pass

        if args.out_jsonl:
            _jsonl_append(
                args.out_jsonl,
                {
                    "kind": "coords.watch",
                    "ts": ts,
                    "coords": list(coords) if coords is not None else None,
                    "changed": bool(changed),
                    "ok": bool(coords is not None),
                },
            )

        dt = time.time() - t0
        to_sleep = max(0.0, interval_s - dt)
        if to_sleep:
            time.sleep(to_sleep)

        idx += 1

    ok_rate = (float(ok) / float(total)) if total else 0.0
    print(f"done samples={total} ok={ok} ok_rate={ok_rate:.1%} changes={changes} last={last}")

    # Audible cue: sampling end.
    if args.beep_on_change:
        try:
            import winsound

            winsound.Beep(440, 160)
        except Exception:
            try:
                print("\a", end="")
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
