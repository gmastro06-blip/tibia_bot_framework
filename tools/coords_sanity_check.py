from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

import numpy as np


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


def _load_config(path: str) -> tuple[dict, list[int]]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg.get("rois_guess_norm", cfg.get("rois", {})), cfg.get("source_resolution", [0, 0])


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-sample coords OCR sanity check against a live capture + current ROIs.")
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument("--out-dir", type=str, default=str(Path("logs") / "coords_sanity"))
    p.add_argument(
        "--rois",
        type=str,
        default="",
        help="Path to ROI config JSON. If omitted, uses ROIS_CONFIG env var, else auto-picks by resolution.",
    )
    p.add_argument("--retries", type=int, default=60)
    p.add_argument("--samples", type=int, default=30)
    p.add_argument("--interval-ms", type=int, default=120)
    p.add_argument("--warn-jump", type=int, default=int(float(os.getenv("ASSIST_COORDS_WARN_JUMP", "6") or 6)))
    p.add_argument("--fail-jump", type=int, default=int(float(os.getenv("ASSIST_COORDS_FAIL_JUMP", "25") or 25)))
    p.add_argument("--save-overlay", action="store_true")
    p.add_argument("--save-crops", action="store_true")
    p.add_argument("--save-frame", action="store_true", help="Save the base frame.png used for this run (debug).")
    return p.parse_args()


def _crop(frame: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = rect
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(int(frame.shape[1]), x0 + max(1, int(w)))
    y1 = min(int(frame.shape[0]), y0 + max(1, int(h)))
    return frame[y0:y1, x0:x1].copy()


def _expand_rect(
    frame: np.ndarray,
    rect: tuple[int, int, int, int],
    *,
    pad_x_frac: float,
    pad_y_frac: float,
) -> tuple[int, int, int, int]:
    x, y, w, h = rect
    x0 = max(0, int(x - (w * pad_x_frac)))
    y0 = max(0, int(y - (h * pad_y_frac)))
    x1 = min(int(frame.shape[1]), int(x + w + (w * pad_x_frac)))
    y1 = min(int(frame.shape[0]), int(y + h + (h * pad_y_frac)))
    return x0, y0, max(1, int(x1 - x0)), max(1, int(y1 - y0))


def _jump(a: tuple[int, int, int | None], b: tuple[int, int, int | None]) -> int:
    dx = abs(int(a[0]) - int(b[0]))
    dy = abs(int(a[1]) - int(b[1]))
    z_changed = a[2] is not None and b[2] is not None and int(a[2]) != int(b[2])
    return int(dx + dy + (50 if z_changed else 0))


def _suggest_coords_roi(
    *,
    frame: np.ndarray,
    ocr,
    min_xy: int,
    max_xy: int,
    max_z: int,
) -> tuple[dict[str, float] | None, tuple[int, int, int | None] | None]:
    """Heurística: buscar coords (x,y[,z]) en el HUD derecho.

    Devuelve (roi_norm, coords) o (None, None).
    """

    h, w = int(frame.shape[0]), int(frame.shape[1])
    # Región razonable: derecha + zona superior donde suele estar minimapa/coords.
    rx0 = int(w * 0.78)
    ry0 = 0
    rx1 = w
    ry1 = int(h * 0.35)
    region = frame[ry0:ry1, rx0:rx1]
    if region is None or getattr(region, "size", 0) == 0:
        return None, None

    try:
        import cv2

        # Upscale moderado para fuentes pequeñas.
        region_up = cv2.resize(region, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        scale = 2.0
    except Exception:
        region_up = region
        scale = 1.0

    try:
        results = ocr.reader.readtext(region_up, detail=1, allowlist="0123456789XYZxyz:,- ")
    except Exception:
        results = []

    items: list[dict[str, Any]] = []
    for bbox, text, conf in results or []:
        try:
            s = str(text or "").strip()
            if not s:
                continue
            nums = [int(n) for n in __import__("re").findall(r"\d+", s)]
            if not nums:
                continue
            xs = [float(p[0]) for p in bbox]
            ys = [float(p[1]) for p in bbox]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            items.append(
                {
                    "bbox": (x0, y0, x1, y1),
                    "text": s,
                    "conf": float(conf or 0.0),
                    "nums": nums,
                    "y_mid": float((y0 + y1) / 2.0),
                }
            )
        except Exception:
            continue

    if not items:
        return None, None

    # Agrupar por líneas según y_mid.
    items.sort(key=lambda it: float(it.get("y_mid", 0.0)))
    lines: list[list[dict[str, Any]]] = []
    for it in items:
        if not lines:
            lines.append([it])
            continue
        if abs(float(it["y_mid"]) - float(lines[-1][-1]["y_mid"])) <= 18.0:
            lines[-1].append(it)
        else:
            lines.append([it])

    best: tuple[float, dict[str, float], tuple[int, int, int | None]] | None = None

    for line in lines:
        # ordenar por x
        line_sorted = sorted(line, key=lambda it: float(it["bbox"][0]))
        nums_big: list[tuple[int, dict[str, Any]]] = []
        nums_small: list[int] = []
        for it in line_sorted:
            for n in it["nums"]:
                if min_xy <= int(n) <= max_xy:
                    nums_big.append((int(n), it))
                if 0 <= int(n) <= int(max_z):
                    nums_small.append(int(n))
        if len(nums_big) < 2:
            continue

        x_val = int(nums_big[0][0])
        y_val = int(nums_big[1][0])
        z_val: int | None = nums_small[0] if nums_small else None
        coords = (x_val, y_val, z_val)

        # bbox union de los 2 tokens grandes (y opcionalmente el token que contiene z)
        picked_boxes = [nums_big[0][1]["bbox"], nums_big[1][1]["bbox"]]
        if z_val is not None:
            try:
                for it in line_sorted:
                    if z_val in it.get("nums", []):
                        picked_boxes.append(it["bbox"])
                        break
            except Exception:
                pass

        x0 = min(b[0] for b in picked_boxes)
        y0 = min(b[1] for b in picked_boxes)
        x1 = max(b[2] for b in picked_boxes)
        y1 = max(b[3] for b in picked_boxes)

        # padding en coords del region_up
        pad = 10.0
        x0 -= pad
        y0 -= pad
        x1 += pad
        y1 += pad

        # mapear a frame
        fx0 = int(rx0 + (x0 / scale))
        fy0 = int(ry0 + (y0 / scale))
        fx1 = int(rx0 + (x1 / scale))
        fy1 = int(ry0 + (y1 / scale))
        fx0 = max(0, min(fx0, w - 2))
        fy0 = max(0, min(fy0, h - 2))
        fx1 = max(fx0 + 1, min(fx1, w - 1))
        fy1 = max(fy0 + 1, min(fy1, h - 1))

        roi_norm = {
            "x": float(fx0) / float(w),
            "y": float(fy0) / float(h),
            "w": float(fx1 - fx0) / float(w),
            "h": float(fy1 - fy0) / float(h),
        }

        # score: confianza + preferir 5 dígitos
        score = 0.0
        try:
            score += float(nums_big[0][1].get("conf", 0.0)) + float(nums_big[1][1].get("conf", 0.0))
        except Exception:
            pass
        score += 0.25 * float(len(str(abs(x_val)))) + 0.25 * float(len(str(abs(y_val))))

        if best is None or score > best[0]:
            best = (score, roi_norm, coords)

    if best is None:
        return None, None
    return best[1], best[2]


def main() -> int:
    _add_src_to_syspath()

    import cv2

    from capture.dxgi_capture import DXGICapture
    from vision.ocr import OCRProcessor

    args = _parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = DXGICapture(force_monitor=args.monitor)

    frame: np.ndarray | None = None
    for _ in range(max(1, int(args.retries))):
        frame = cap.capture()
        if frame is not None:
            break
        time.sleep(0.05)

    if frame is None:
        print("FAIL: no frame captured")
        return 2

    resolution = (int(frame.shape[1]), int(frame.shape[0]))
    cfg_path = _resolve_rois_path(args.rois) or _resolve_rois_path(os.getenv("ROIS_CONFIG", "")) or _pick_config_file(resolution)

    rois, source_resolution = _load_config(cfg_path)
    rois = dict(rois)
    rois["_source_resolution"] = source_resolution

    ts = float(time.time())
    base_dir = out_dir / f"{ts:.6f}"
    base_dir.mkdir(parents=True, exist_ok=True)

    if args.save_frame:
        try:
            cv2.imwrite(str(base_dir / "frame.png"), frame)
        except Exception:
            pass

    ocr = OCRProcessor()

    # thresholds compatibles con OCRProcessor.extract_coords
    try:
        min_xy = int(float(os.getenv("COORDS_MIN_XY", "20000").strip() or "20000"))
    except Exception:
        min_xy = 20000
    try:
        max_xy = int(float(os.getenv("COORDS_MAX_XY", "100000").strip() or "100000"))
    except Exception:
        max_xy = 100000
    try:
        max_z = int(float(os.getenv("COORDS_MAX_Z", "15").strip() or "15"))
    except Exception:
        max_z = 15

    n = max(5, int(args.samples))
    interval_s = max(0.02, float(int(args.interval_ms)) / 1000.0)
    warn_jump = max(1, int(args.warn_jump))
    fail_jump = max(warn_jump, int(args.fail_jump))

    series: list[dict[str, Any]] = []
    coords_list: list[tuple[int, int, int | None] | None] = []

    crops_dir = base_dir / "crops"
    if args.save_crops:
        crops_dir.mkdir(parents=True, exist_ok=True)

    for i in range(n):
        t0 = time.time()
        f = frame if i == 0 else cap.capture()

        if f is None:
            coords = None
        else:
            try:
                coords = ocr.extract_coords(f, rois, resolution)
            except Exception:
                coords = None

        coords_list.append(coords)
        series.append({"i": i, "ts": time.time(), "coords": list(coords) if coords is not None else None})

        if args.save_crops and f is not None:
            try:
                if rois.get("coords_ocr") is not None:
                    rect = ocr._roi_to_px(f, rois, resolution, rois["coords_ocr"])  # type: ignore[index]
                    crop = _crop(f, rect)
                    if crop.size:
                        fail = coords is None
                        if i < 12 or (fail and i < 24):
                            cv2.imwrite(str(crops_dir / f"{i:03d}_{'fail' if fail else 'ok'}.png"), crop)

                        # Guardar el crop expandido/escalado que usa extract_coords (útil para diagnosticar).
                        if i == 0 or (fail and i == 1):
                            expanded_rect = _expand_rect(f, rect, pad_x_frac=0.25, pad_y_frac=0.50)
                            exp_crop = _crop(f, expanded_rect)
                            if exp_crop.size:
                                cv2.imwrite(str(crops_dir / f"{i:03d}_expanded.png"), exp_crop)
                                try:
                                    scaled = cv2.resize(exp_crop, (0, 0), fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                                    cv2.imwrite(str(crops_dir / f"{i:03d}_expanded_scaled.png"), scaled)
                                except Exception:
                                    pass
            except Exception:
                pass

        dt = time.time() - t0
        to_sleep = max(0.0, interval_s - dt)
        if to_sleep:
            time.sleep(to_sleep)

    ok_coords = [c for c in coords_list if c is not None]
    ok_count = len(ok_coords)
    ok_rate = float(ok_count) / float(n) if n else 0.0

    jumps: list[int] = []
    warn_jumps = 0
    fail_jumps = 0
    last_valid: tuple[int, int, int | None] | None = None

    for c in coords_list:
        if c is None:
            continue
        if last_valid is None:
            last_valid = c
            continue
        j = _jump(c, last_valid)
        jumps.append(j)
        if j >= fail_jump:
            fail_jumps += 1
        elif j >= warn_jump:
            warn_jumps += 1
        if j < warn_jump:
            last_valid = c

    max_jump = max(jumps) if jumps else 0
    mean_jump = (sum(jumps) / float(len(jumps))) if jumps else 0.0

    uniq = 0
    try:
        uniq = len({(int(c[0]), int(c[1]), int(c[2]) if c[2] is not None else None) for c in ok_coords})
    except Exception:
        uniq = 0

    roi_debug: dict[str, Any] = {}
    try:
        if rois.get("coords_ocr") is not None:
            rect = ocr._roi_to_px(frame, rois, resolution, rois["coords_ocr"])  # type: ignore[index]
            expanded_rect = _expand_rect(frame, rect, pad_x_frac=0.25, pad_y_frac=0.50)
            roi_debug = {
                "coords_ocr_px": [int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])],
                "coords_ocr_expanded_px": [
                    int(expanded_rect[0]),
                    int(expanded_rect[1]),
                    int(expanded_rect[2]),
                    int(expanded_rect[3]),
                ],
            }
    except Exception:
        roi_debug = {}

    if args.save_overlay:
        try:
            overlay = frame.copy()
            if rois.get("coords_ocr") is not None and isinstance(rois.get("coords_ocr"), Mapping):
                x, y, w, h = ocr._roi_to_px(frame, rois, resolution, rois["coords_ocr"])  # type: ignore[arg-type]
                cv2.rectangle(overlay, (int(x), int(y)), (int(x + w), int(y + h)), (0, 255, 0), 2)
                cv2.putText(
                    overlay,
                    "coords_ocr",
                    (int(x), max(12, int(y) - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )
            label = f"ok_rate={ok_rate:.2f} max_jump={max_jump} warn={warn_jumps} fail={fail_jumps}"
            cv2.putText(overlay, label, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            cv2.imwrite(str(base_dir / "overlay.png"), overlay)
        except Exception:
            pass

    report = {
        "ts": ts,
        "config": cfg_path,
        "resolution": list(resolution),
        "source_resolution": source_resolution,
        "roi_debug": roi_debug,
        "suggested": {},
        "coords_sanity": {
            "samples": n,
            "ok": ok_count,
            "ok_rate": ok_rate,
            "warn_jump": warn_jump,
            "fail_jump": fail_jump,
            "warn_jumps": warn_jumps,
            "fail_jumps": fail_jumps,
            "max_jump": max_jump,
            "mean_jump": mean_jump,
            "unique_coords": uniq,
            "last_coords": (list(ok_coords[-1]) if ok_coords else None),
        },
        "series": series,
    }

    # Si no hay coords, intentamos sugerir un mejor ROI para `coords_ocr`.
    try:
        if ok_count == 0:
            suggested_roi, suggested_coords = _suggest_coords_roi(
                frame=frame,
                ocr=ocr,
                min_xy=int(min_xy),
                max_xy=int(max_xy),
                max_z=int(max_z),
            )
            if suggested_roi is not None:
                report["suggested"] = {
                    "coords_ocr": suggested_roi,
                    "coords_value": list(suggested_coords) if suggested_coords is not None else None,
                }
                # Escribir archivo listo para usar con ROIS_CONFIG
                try:
                    suggested_path = base_dir / "suggested_rois.json"
                    suggested_payload = {
                        "source_resolution": list(resolution),
                        "rois_guess_norm": {"coords_ocr": suggested_roi},
                    }
                    suggested_path.write_text(
                        json.dumps(suggested_payload, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    print(f"SUGGEST_FILE: {suggested_path}")
                except Exception:
                    pass
                print(f"SUGGEST coords_ocr: {json.dumps(suggested_roi)}")
    except Exception:
        pass
    (base_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OUT_DIR: {base_dir}")
    print(f"Config: {cfg_path} | frame={resolution[0]}x{resolution[1]} source={source_resolution}")
    print(f"Coords ok: {ok_count}/{n} ({ok_rate:.2%}) | jumps max={max_jump} warn={warn_jumps} fail={fail_jumps}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
