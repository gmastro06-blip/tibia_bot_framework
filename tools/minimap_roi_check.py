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


def _load_config(path: str) -> tuple[dict[str, Any], list[int]]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    rois = cfg.get("rois_guess_norm", cfg.get("rois", cfg))
    source_resolution = cfg.get("source_resolution", [0, 0])
    if not isinstance(rois, dict):
        rois = {}
    return dict(rois), list(source_resolution) if isinstance(source_resolution, list) else [0, 0]


def _roi_to_px(
    frame: Any,
    rois: Mapping[str, Any],
    resolution: tuple[int, int],
    roi_def: Mapping[str, Any],
) -> tuple[int, int, int, int]:
    """Convierte una ROI (normalizada o px) al frame actual, compensando letterboxing.

    Nota: implementado localmente para no cargar OCRProcessor/EasyOCR.
    """

    frame_w, frame_h = int(frame.shape[1]), int(frame.shape[0])

    source_resolution = rois.get("_source_resolution") if hasattr(rois, "get") else None
    if (
        isinstance(source_resolution, (list, tuple))
        and len(source_resolution) == 2
        and source_resolution[0]
        and source_resolution[1]
    ):
        source_w, source_h = int(source_resolution[0]), int(source_resolution[1])
    else:
        source_w, source_h = int(resolution[0]), int(resolution[1])

    scale = min(frame_w / source_w, frame_h / source_h) if source_w and source_h else 1.0
    content_w = source_w * scale
    content_h = source_h * scale
    offset_x = (frame_w - content_w) / 2.0
    offset_y = (frame_h - content_h) / 2.0

    unit = str(roi_def.get("unit", "") if hasattr(roi_def, "get") else "").lower()
    x_val = roi_def.get("x") if hasattr(roi_def, "get") else None
    y_val = roi_def.get("y") if hasattr(roi_def, "get") else None
    w_val = roi_def.get("w") if hasattr(roi_def, "get") else None
    h_val = roi_def.get("h") if hasattr(roi_def, "get") else None

    def _f(v: Any, default: float) -> float:
        try:
            if v is None:
                return default
            return float(v)
        except Exception:
            return default

    def _is_normalized(v: Any) -> bool:
        try:
            vf = float(v)
        except Exception:
            return False
        return 0.0 <= vf <= 1.0

    is_norm = (
        unit != "px"
        and _is_normalized(x_val)
        and _is_normalized(y_val)
        and _is_normalized(w_val)
        and _is_normalized(h_val)
    )

    if is_norm:
        x_src = _f(x_val, 0.0) * source_w
        y_src = _f(y_val, 0.0) * source_h
        w_src = _f(w_val, 0.0) * source_w
        h_src = _f(h_val, 0.0) * source_h
    else:
        x_src = _f(x_val, 0.0)
        y_src = _f(y_val, 0.0)
        w_src = _f(w_val, 0.0)
        h_src = _f(h_val, 0.0)

    # Offset global (en px del source), aplicado a todas las ROIs.
    try:
        off = rois.get("_roi_offset_px") if hasattr(rois, "get") else None
        if isinstance(off, (list, tuple)) and len(off) == 2:
            x_src += float(off[0] or 0.0)
            y_src += float(off[1] or 0.0)
    except Exception:
        pass

    x = int(round(offset_x + x_src * scale))
    y = int(round(offset_y + y_src * scale))
    w = int(round(w_src * scale))
    h = int(round(h_src * scale))

    x = max(0, min(x, frame_w - 1))
    y = max(0, min(y, frame_h - 1))
    w = max(1, min(w, frame_w - x))
    h = max(1, min(h, frame_h - y))
    return x, y, w, h


def _crop(frame: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = rect
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(int(frame.shape[1]), x0 + max(1, int(w)))
    y1 = min(int(frame.shape[0]), y0 + max(1, int(h)))
    return frame[y0:y1, x0:x1].copy()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Valida si la ROI minimap_content realmente cambia en el tiempo (sin inputs). "
            "Guarda crops/diffs para calibración rápida."
        )
    )
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--interval-ms", type=int, default=250)
    p.add_argument(
        "--rois",
        type=str,
        default="",
        help="Path al JSON de ROIs. Si se omite, usa ROIS_CONFIG o auto por resolución.",
    )
    p.add_argument("--name", type=str, default="minimap_content", help="Nombre de ROI a chequear")
    p.add_argument("--out-dir", type=str, default=str(Path("logs") / "minimap_roi_check"))
    p.add_argument("--save-overlay", action="store_true", help="Guardar overlay.png con la caja de la ROI")
    p.add_argument("--save-diff", action="store_true", help="Guardar diff.png (absdiff) cuando cambia")
    p.add_argument(
        "--save-every",
        type=int,
        default=0,
        help="Guardar un crop cada N samples (0 = solo cuando cambia + primeros 2)",
    )
    p.add_argument(
        "--check-frame",
        action="store_true",
        help="También mide si el frame completo cambia (detecta captura congelada / pantalla estática).",
    )
    p.add_argument("--diff-th", type=float, default=2.0, help="Umbral (mean abs diff) para considerar 'cambió'")
    p.add_argument("--print", action="store_true", help="Imprimir mediciones por consola")
    return p.parse_args()


def main() -> int:
    _add_src_to_syspath()

    import cv2

    from capture.dxgi_capture import DXGICapture

    args = _parse_args()

    cap = DXGICapture(force_monitor=int(args.monitor), strict_force_monitor=True)

    frame = None
    for _ in range(80):
        frame = cap.capture()
        if frame is not None:
            break
        time.sleep(0.05)

    if frame is None:
        print("No pude capturar ningún frame. Revisa FORCE_MONITOR/ventana visible.")
        return 2

    resolution = (int(frame.shape[1]), int(frame.shape[0]))

    rois_path = _resolve_rois_path(args.rois) or _resolve_rois_path(os.getenv("ROIS_CONFIG", ""))
    if not rois_path:
        rois_path = _pick_config_file(resolution)

    rois, source_resolution = _load_config(rois_path)
    rois["_source_resolution"] = source_resolution

    name = str(args.name or "minimap_content").strip() or "minimap_content"
    if name not in rois:
        print(f"El config {rois_path} no tiene ROI '{name}'.")
        return 3

    rect = _roi_to_px(frame, rois, resolution, rois[name])

    base_dir = Path(args.out_dir) / f"{time.time():.6f}"
    crops_dir = base_dir / "crops"
    diffs_dir = base_dir / "diffs"
    base_dir.mkdir(parents=True, exist_ok=True)

    # Guardar overlay + primer crop.
    try:
        crops_dir.mkdir(parents=True, exist_ok=True)
        crop0 = _crop(frame, rect)
        cv2.imwrite(str(crops_dir / "000.png"), crop0)
    except Exception:
        crop0 = None

    if bool(args.save_overlay):
        try:
            overlay = frame.copy()
            x, y, w, h = rect
            cv2.rectangle(overlay, (int(x), int(y)), (int(x + w), int(y + h)), (0, 255, 0), 2)
            cv2.putText(
                overlay,
                f"{name} {rect}",
                (max(5, int(x)), max(20, int(y - 6))),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )
            cv2.imwrite(str(base_dir / "overlay.png"), overlay)
        except Exception:
            pass

    meta = {
        "ts": time.time(),
        "monitor": int(args.monitor),
        "resolution": [int(resolution[0]), int(resolution[1])],
        "config": rois_path,
        "source_resolution": source_resolution,
        "roi_name": name,
        "roi_rect_px": [int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])],
        "diff_th": float(args.diff_th),
    }
    (base_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    interval_s = max(0.05, float(args.interval_ms) / 1000.0)
    t_end = time.time() + max(1.0, float(args.seconds))

    prev_crop = crop0
    prev_frame_sig: np.ndarray | None = None
    n = 1
    changed = 0
    max_mean = 0.0

    frame_changed = 0
    max_frame_mean = 0.0

    if args.print:
        print(
            f"monitor={args.monitor} res={resolution[0]}x{resolution[1]} config={rois_path} roi={name} rect={rect}"
        )
        print(
            "Camina 10–20 tiles mientras corre. Si los diffs quedan ~0, la ROI no apunta al contenido del minimapa."
        )

    while time.time() < t_end:
        t0 = time.time()
        f = cap.capture()
        if f is None:
            time.sleep(interval_s)
            continue

        frame_mean_abs = 0.0
        if bool(args.check_frame):
            try:
                # Cheap signature: downscale grayscale for stable diff.
                sig = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                sig = cv2.resize(sig, (160, 90), interpolation=cv2.INTER_AREA)
                sig = sig.astype(np.float32)
                if prev_frame_sig is not None and prev_frame_sig.shape == sig.shape:
                    frame_mean_abs = float(np.mean(np.abs(sig - prev_frame_sig)))
                prev_frame_sig = sig
            except Exception:
                frame_mean_abs = 0.0

            max_frame_mean = max(max_frame_mean, float(frame_mean_abs))
            if frame_mean_abs > 0.0:
                frame_changed += 1

        crop = None
        try:
            crop = _crop(f, rect)
        except Exception:
            crop = None

        if crop is None or getattr(crop, "size", 0) == 0:
            time.sleep(interval_s)
            continue

        mean_abs = 0.0
        if prev_crop is not None and prev_crop.shape == crop.shape:
            try:
                diff = cv2.absdiff(prev_crop, crop)
                mean_abs = float(np.mean(diff.astype(np.float32)))
            except Exception:
                mean_abs = 0.0

        max_mean = max(max_mean, mean_abs)
        is_changed = bool(mean_abs >= float(args.diff_th))
        if is_changed:
            changed += 1

        if args.print:
            extra = ""
            if bool(args.check_frame):
                extra = f" frame_mean_abs_diff={float(frame_mean_abs):.3f}"
            print(f"#{n:03d} mean_abs_diff={mean_abs:.3f}{extra}" + ("  CHANGED" if is_changed else ""))

        # Guardar crops cuando cambió (y siempre el primer par de mediciones).
        try:
            crops_dir.mkdir(parents=True, exist_ok=True)
            save_every = int(args.save_every or 0)
            periodic = bool(save_every > 0 and (n % save_every) == 0)
            if is_changed or n <= 2 or periodic:
                cv2.imwrite(str(crops_dir / f"{n:03d}.png"), crop)
            if bool(args.save_diff) and is_changed and prev_crop is not None and prev_crop.shape == crop.shape:
                diffs_dir.mkdir(parents=True, exist_ok=True)
                diff = cv2.absdiff(prev_crop, crop)
                cv2.imwrite(str(diffs_dir / f"{n:03d}.png"), diff)
        except Exception:
            pass

        prev_crop = crop
        n += 1

        elapsed = time.time() - t0
        to_sleep = max(0.0, interval_s - elapsed)
        if to_sleep:
            time.sleep(to_sleep)

    tip = (
        "OK: la ROI cambia (mínimo a veces)."
        if changed > 0
        else "FAIL: la ROI nunca cambió; ajusta minimap_content con tools/select_roi.py"
    )
    if changed == 0 and bool(args.check_frame) and float(max_frame_mean) <= 0.0:
        tip = (
            "FAIL: ni la ROI ni el frame parecen cambiar (captura congelada o pantalla estática). "
            "Verifica que Tibia se mueva en el monitor correcto y que FORCE_MONITOR/strict_force_monitor estén bien."
        )

    report = {
        "total_samples": int(n),
        "changed_samples": int(changed),
        "max_mean_abs_diff": float(max_mean),
        "check_frame": bool(args.check_frame),
        "frame_changed_samples": int(frame_changed),
        "frame_max_mean_abs_diff": float(max_frame_mean),
        "tip": tip,
    }
    (base_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Listo. samples={n} changed={changed} max_mean_abs_diff={max_mean:.3f} out={base_dir}"
    )
    if changed == 0:
        print(
            "Siguiente paso recomendado: recalibrar 'minimap_content' con:\n"
            "  poetry run python tools/select_roi.py --monitor 2 --name minimap_content --base right_hud_panel --write\n"
            "Luego re-ejecuta este check."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
