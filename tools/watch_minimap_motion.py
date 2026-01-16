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


def _jsonl_append(path: str, event: dict[str, Any]) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _coords_from_json_file(path_raw: str) -> tuple[int, int, int | None] | None:
    raw = (path_raw or "").strip()
    if not raw:
        return None
    try:
        p = Path(raw)
        if not p.is_absolute():
            repo_root = Path(__file__).resolve().parent.parent
            p = (repo_root / p).resolve()
        if not p.exists() or not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

    try:
        if isinstance(data, dict):
            if "coords" in data:
                data = data.get("coords")
            if isinstance(data, dict) and ("x" in data and "y" in data):
                x_raw = data.get("x")
                y_raw = data.get("y")
                if x_raw is None or y_raw is None:
                    return None
                x = int(x_raw)
                y = int(y_raw)
                z_raw = data.get("z", None)
                z = int(z_raw) if z_raw is not None else None
                return (x, y, z)
        if isinstance(data, (list, tuple)) and len(data) >= 2:
            x = int(data[0])
            y = int(data[1])
            z = int(data[2]) if len(data) >= 3 and data[2] is not None else None
            return (x, y, z)
    except Exception:
        return None
    return None


def _write_coords_file(path_raw: str, *, coords: tuple[int, int, int | None], ts: float) -> None:
    out = (path_raw or "").strip()
    if not out:
        return

    try:
        p = Path(out)
        if not p.is_absolute():
            repo_root = Path(__file__).resolve().parent.parent
            p = (repo_root / p).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "x": int(coords[0]),
            "y": int(coords[1]),
            "z": (int(coords[2]) if coords[2] is not None else None),
            "ts": float(ts),
            "source": "minimap",
        }
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            tmp.replace(p)
        except Exception:
            p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        return


def _roi_to_px(
    frame: Any,
    rois: Mapping[str, Any],
    resolution: tuple[int, int],
    roi_def: Mapping[str, Any],
) -> tuple[int, int, int, int]:
    """Convierte una ROI (normalizada o px) al frame actual, compensando letterboxing.

    Nota: duplicado a propósito para no cargar OCRProcessor/EasyOCR.
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


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Log de movimiento del minimapa via phase correlation (sin inputs). "
            "Útil para calibrar MINIMAP_TILE_PX / MINIMAP_PHASECORR_MIN_RESPONSE."
        )
    )
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--interval-ms", type=int, default=120)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="No captura: solo valida ROIs/seed/out paths y sale.",
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
        default=str(Path("logs") / "minimap_motion.jsonl"),
        help="Ruta del JSONL de salida.",
    )
    p.add_argument("--seed-x", type=int, default=None, help="Seed X (coords absolutas).")
    p.add_argument("--seed-y", type=int, default=None, help="Seed Y (coords absolutas).")
    p.add_argument("--seed-z", type=int, default=None, help="Seed Z (opcional).")
    p.add_argument(
        "--seed-file",
        type=str,
        default="",
        help="Archivo JSON con seed {x,y,z} o [x,y,z]. Si no se pasa, usa COORDS_SEED_FILE.",
    )
    p.add_argument(
        "--out-coords",
        type=str,
        default="",
        help="(Opcional) Escribe coords absolutas actuales a este JSON en cada update aceptado.",
    )
    p.add_argument(
        "--save-crops",
        action="store_true",
        help="Guardar crops del minimapa cuando se acepta un step.",
    )
    p.add_argument(
        "--crops-dir",
        type=str,
        default=str(Path("logs") / "minimap_motion_crops"),
        help="Directorio donde guardar crops si usas --save-crops.",
    )
    p.add_argument(
        "--max-crops",
        type=int,
        default=60,
        help="Máximo de crops guardados (eventos aceptados).",
    )
    p.add_argument(
        "--print",
        action="store_true",
        help="Imprimir steps aceptados por consola.",
    )
    p.add_argument(
        "--log-all",
        action="store_true",
        help=(
            "Loggea cada muestra al JSONL (incluye diag + estado interno del tracker), "
            "incluso si no se acepta ningún step. Útil para tunear umbrales."
        ),
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    # Importante: dejar --help instantáneo (argparse sale antes de llegar acá).
    _add_src_to_syspath()

    from capture.dxgi_capture import DXGICapture
    from vision.minimap_motion import MinimapMotionTracker

    import cv2
    import numpy as np

    cap = DXGICapture(force_monitor=int(args.monitor), strict_force_monitor=True)
    tracker = MinimapMotionTracker()

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

    if "minimap_content" not in rois:
        print(f"El config {rois_path} no tiene ROI 'minimap_content'.")
        return 3

    # Precompute pixel rect once.
    rect_px: tuple[int, int, int, int] | None = None
    try:
        rect_px = _roi_to_px(frame, rois, resolution, rois["minimap_content"])
    except Exception:
        rect_px = None

    if rect_px is None:
        print("No pude convertir minimap_content a px. Revisa ROIs/source_resolution.")
        return 4

    out_jsonl = str(args.out_jsonl)
    interval_s = max(0.05, float(args.interval_ms) / 1000.0)
    t_end = time.time() + max(1.0, float(args.seconds))

    crops_dir = Path(args.crops_dir) / f"{time.time():.6f}"
    crops_saved = 0

    # Helper: compute raw phase-correlation diagnostics even for rejected steps.
    prev_gray: np.ndarray | None = None
    window: np.ndarray | None = None

    def _to_gray_f32(img_bgr: Any) -> np.ndarray | None:
        try:
            if img_bgr is None or getattr(img_bgr, "size", 0) == 0:
                return None
            if getattr(img_bgr, "ndim", 0) == 2:
                gray = img_bgr
            else:
                gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            return gray.astype(np.float32)
        except Exception:
            return None

    def _phase_diag(img_bgr: Any) -> tuple[float | None, float | None, float | None, int | None, int | None]:
        """Devuelve (resp, shift_x_px, shift_y_px, dx_tiles, dy_tiles) o Nones."""
        nonlocal prev_gray, window

        cur = _to_gray_f32(img_bgr)
        if cur is None:
            return None, None, None, None, None

        # Hanning window to reduce edge artifacts.
        try:
            if window is None or window.shape != cur.shape:
                h, w = int(cur.shape[0]), int(cur.shape[1])
                wy = np.hanning(h).astype(np.float32)
                wx = np.hanning(w).astype(np.float32)
                window = (wy[:, None] * wx[None, :]).astype(np.float32)
        except Exception:
            window = None

        try:
            cur_w = (cur * window) if window is not None else cur
        except Exception:
            cur_w = cur

        if prev_gray is None:
            prev_gray = cur_w
            return None, None, None, None, None

        prev = prev_gray
        prev_gray = cur_w

        try:
            (shift_x, shift_y), response = cv2.phaseCorrelate(prev, cur_w)
        except Exception:
            return None, None, None, None, None

        try:
            resp = float(response)
        except Exception:
            resp = None

        try:
            dx_px = float(shift_x)
            dy_px = float(shift_y)
        except Exception:
            return resp, None, None, None, None

        tile_px = max(1, int(getattr(tracker.cfg, "tile_px", 4) or 4))
        dx_tiles = int(round((-dx_px) / float(tile_px)))
        dy_tiles = int(round((-dy_px) / float(tile_px)))

        if bool(getattr(tracker.cfg, "invert_x", False)):
            dx_tiles = -dx_tiles
        if bool(getattr(tracker.cfg, "invert_y", False)):
            dy_tiles = -dy_tiles

        return resp, dx_px, dy_px, dx_tiles, dy_tiles

    # Seed (opcional) para coords absolutas.
    seed: tuple[int, int, int | None] | None = None
    seed_file = (args.seed_file or "").strip() or (os.getenv("COORDS_SEED_FILE", "") or "").strip()
    if seed_file:
        seed = _coords_from_json_file(seed_file)
    if seed is None:
        sx = args.seed_x
        sy = args.seed_y
        sz = args.seed_z
        if sx is None:
            try:
                sx = int((os.getenv("COORDS_SEED_X", "") or "").strip() or "0")
            except Exception:
                sx = None
        if sy is None:
            try:
                sy = int((os.getenv("COORDS_SEED_Y", "") or "").strip() or "0")
            except Exception:
                sy = None
        if sz is None:
            rawz = (os.getenv("COORDS_SEED_Z", "") or "").strip()
            if rawz:
                try:
                    sz = int(rawz)
                except Exception:
                    sz = None
        if sx is not None and sy is not None:
            seed = (int(sx), int(sy), int(sz) if sz is not None else None)

    abs_coords = seed
    abs_enabled = bool(seed is not None)
    abs_updates = 0
    net_dx = 0
    net_dy = 0

    # Dry-run: validar y salir sin capturar.
    if bool(args.dry_run):
        print(
            f"OK dry-run | monitor={args.monitor} | res={resolution[0]}x{resolution[1]} | config={rois_path} | "
            f"minimap_px={rect_px} | seed={'OK' if abs_enabled else 'MISSING'}"
        )
        if abs_enabled and abs_coords is not None:
            print(
                f"seed: x={abs_coords[0]} y={abs_coords[1]}"
                + (f" z={abs_coords[2]}" if abs_coords[2] is not None else "")
            )
        if (args.out_coords or "").strip() and abs_enabled and abs_coords is not None:
            try:
                _write_coords_file(str(args.out_coords), coords=abs_coords, ts=time.time())
                print(f"out_coords: OK ({args.out_coords})")
            except Exception:
                print(f"out_coords: ERROR ({args.out_coords})")
        return 0

    meta = {
        "kind": "meta",
        "ts": time.time(),
        "monitor": int(args.monitor),
        "resolution": [int(resolution[0]), int(resolution[1])],
        "config": rois_path,
        "source_resolution": source_resolution,
        "minimap_content_px": [int(rect_px[0]), int(rect_px[1]), int(rect_px[2]), int(rect_px[3])],
        "minimap_cfg": {
            "mode": str(getattr(tracker.cfg, "mode", "auto")),
            "tile_px": int(tracker.cfg.tile_px),
            "min_response": float(tracker.cfg.min_response),
            "max_shift_px": float(tracker.cfg.max_shift_px),
            "max_step_per_frame": int(tracker.cfg.max_step_per_frame),
            "emit_threshold_tiles": float(getattr(tracker.cfg, "emit_threshold_tiles", 0.5) or 0.5),
            "deadband_tiles": float(getattr(tracker.cfg, "deadband_tiles", 0.0) or 0.0),
            "invert_x": bool(tracker.cfg.invert_x),
            "invert_y": bool(tracker.cfg.invert_y),
            "marker_v_min": int(getattr(tracker.cfg, "marker_v_min", 0) or 0),
            "marker_s_max": int(getattr(tracker.cfg, "marker_s_max", 0) or 0),
            "marker_area_min": int(getattr(tracker.cfg, "marker_area_min", 0) or 0),
            "marker_area_max": int(getattr(tracker.cfg, "marker_area_max", 0) or 0),
        },
        "abs_seed": {
            "x": (int(seed[0]) if seed is not None else None),
            "y": (int(seed[1]) if seed is not None else None),
            "z": (int(seed[2]) if (seed is not None and seed[2] is not None) else None),
        },
        "abs_enabled": bool(abs_enabled),
    }
    _jsonl_append(out_jsonl, meta)

    total = 0
    accepted = 0
    last_acc = time.time()

    last_crop_for_diag: Any | None = None

    print(
        f"monitor={args.monitor} resolution={resolution[0]}x{resolution[1]} config={rois_path} "
        f"mode={getattr(tracker.cfg, 'mode', 'auto')} tile_px={tracker.cfg.tile_px} min_response={tracker.cfg.min_response} "
        f"emit_thr={float(getattr(tracker.cfg, 'emit_threshold_tiles', 0.5) or 0.5):.2f} deadband={float(getattr(tracker.cfg, 'deadband_tiles', 0.0) or 0.0):.2f}"
    )

    # Quality stats + initial crop (helps calibrate ROI when accepted==0).
    try:
        x0, y0, w0, h0 = rect_px
        init_crop = frame[y0 : y0 + h0, x0 : x0 + w0]
        g0 = _to_gray_f32(init_crop)
        if g0 is not None:
            mn = float(np.min(g0))
            mx = float(np.max(g0))
            sd = float(np.std(g0))
            print(f"minimap stats: gray min={mn:.0f} max={mx:.0f} std={sd:.1f}")
        if args.save_crops:
            try:
                crops_dir.mkdir(parents=True, exist_ok=True)
                p0 = crops_dir / "initial.png"
                cv2.imwrite(str(p0), init_crop)
                crops_saved = max(crops_saved, 1)
            except Exception:
                pass
    except Exception:
        pass
    if abs_enabled and abs_coords is not None:
        print(
            f"Seed activa: x={abs_coords[0]} y={abs_coords[1]}"
            + (f" z={abs_coords[2]}" if abs_coords[2] is not None else "")
        )
        if (args.out_coords or "").strip():
            print(f"Escribiendo coords a: {args.out_coords}")
    else:
        print("Sin seed: solo se loggea movimiento (dx/dy).")
    print("Mueve 1–2 tiles (sin abrir ventanas) y mira el JSONL. Ctrl+C para cortar.")

    # Diagnostics window.
    diag_resp_max = 0.0
    diag_last: tuple[float | None, float | None, float | None, int | None, int | None] = (None, None, None, None, None)

    try:
        while time.time() < t_end:
            t0 = time.time()
            f = cap.capture()
            total += 1

            if f is None:
                time.sleep(interval_s)
                continue

            x, y, w, h = rect_px
            crop = f[y : y + h, x : x + w]
            if crop is None or crop.size == 0:
                time.sleep(interval_s)
                continue

            last_crop_for_diag = crop

            # Compute raw diagnostics even if the tracker rejects.
            diag_resp, shift_x_px, shift_y_px, dx_tiles_raw, dy_tiles_raw = _phase_diag(crop)
            if diag_resp is not None:
                try:
                    diag_resp_max = max(float(diag_resp_max), float(diag_resp))
                except Exception:
                    pass
            diag_last = (diag_resp, shift_x_px, shift_y_px, dx_tiles_raw, dy_tiles_raw)

            step = None
            try:
                step = tracker.update(crop)
            except Exception:
                step = None

            if args.log_all:
                try:
                    def _pair_f(v: Any, default: tuple[float, float] = (0.0, 0.0)) -> list[float]:
                        try:
                            if v is None:
                                return [float(default[0]), float(default[1])]
                            return [float(v[0]), float(v[1])]
                        except Exception:
                            return [float(default[0]), float(default[1])]

                    ev_ts = time.time()
                    ev_sample: dict[str, Any] = {
                        "kind": "minimap_sample",
                        "ts": float(ev_ts),
                        "accepted": bool(step is not None),
                        "diag": {
                            "resp": (float(diag_resp) if diag_resp is not None else None),
                            "shift_x_px": (float(shift_x_px) if shift_x_px is not None else None),
                            "shift_y_px": (float(shift_y_px) if shift_y_px is not None else None),
                            "dx_tiles_raw": (int(dx_tiles_raw) if dx_tiles_raw is not None else None),
                            "dy_tiles_raw": (int(dy_tiles_raw) if dy_tiles_raw is not None else None),
                        },
                        "tracker": {
                            "mode_used": str(getattr(tracker, "last_mode_used", "") or ""),
                            "response": float(getattr(tracker, "last_response", 0.0) or 0.0),
                            "shift_px": _pair_f(getattr(tracker, "last_shift_px", None), (0.0, 0.0)),
                            "marker_px": None,
                            "marker_dpx": _pair_f(getattr(tracker, "last_marker_dpx", None), (0.0, 0.0)),
                            "delta_tiles_f": _pair_f(getattr(tracker, "last_delta_tiles_f", None), (0.0, 0.0)),
                            "acc_tiles_f": _pair_f(getattr(tracker, "last_acc_tiles_f", None), (0.0, 0.0)),
                        },
                    }

                    try:
                        m = getattr(tracker, "last_marker_px", None)
                        if m is not None:
                            ev_sample["tracker"]["marker_px"] = [float(m[0]), float(m[1])]
                    except Exception:
                        pass

                    if step is not None:
                        try:
                            dx, dy, step_resp = step
                            ev_sample["step"] = {
                                "dx_tiles": int(dx),
                                "dy_tiles": int(dy),
                                "response": float(step_resp),
                            }
                        except Exception:
                            pass

                    if abs_enabled and abs_coords is not None:
                        try:
                            ev_sample["abs"] = {
                                "x": int(abs_coords[0]),
                                "y": int(abs_coords[1]),
                                "z": (int(abs_coords[2]) if abs_coords[2] is not None else None),
                            }
                        except Exception:
                            pass

                    _jsonl_append(out_jsonl, ev_sample)
                except Exception:
                    pass

            if step is not None:
                dx, dy, step_resp = step
                accepted += 1
                last_acc = time.time()
                diag_resp_max = 0.0

                ev_abs = None
                if abs_enabled and abs_coords is not None:
                    try:
                        ax, ay, az = abs_coords
                        ax = int(ax) + int(dx)
                        ay = int(ay) + int(dy)
                        abs_coords = (ax, ay, az)
                        abs_updates += 1
                        net_dx += int(dx)
                        net_dy += int(dy)
                        ev_abs = {"x": int(ax), "y": int(ay), "z": (int(az) if az is not None else None)}
                    except Exception:
                        ev_abs = None

                ts_ev = time.time()
                ev = {
                    "kind": "minimap_motion",
                    "ts": ts_ev,
                    "dx_tiles": int(dx),
                    "dy_tiles": int(dy),
                    "response": float(step_resp),
                }
                if ev_abs is not None:
                    ev["abs"] = ev_abs
                _jsonl_append(out_jsonl, ev)

                # (Opcional) escribir coords actuales como JSON compatible con COORDS_FILE.
                if ev_abs is not None and (args.out_coords or "").strip():
                    try:
                        _write_coords_file(str(args.out_coords), coords=abs_coords, ts=ts_ev)  # type: ignore[arg-type]
                    except Exception:
                        pass

                if args.print:
                    if ev_abs is not None:
                        print(f"step dx={dx} dy={dy} resp={step_resp:.3f} -> x={ev_abs['x']} y={ev_abs['y']}")
                    else:
                        print(f"step dx={dx} dy={dy} resp={step_resp:.3f}")

                if args.save_crops and crops_saved < int(args.max_crops):
                    try:
                        crops_dir.mkdir(parents=True, exist_ok=True)
                        p = crops_dir / f"{time.time():.6f}_dx{dx}_dy{dy}_r{step_resp:.3f}.png"
                        cv2.imwrite(str(p), crop)
                        crops_saved += 1
                    except Exception:
                        pass

            # Small heartbeat: if nothing accepted for a while.
            if args.print and (time.time() - last_acc) >= 5.0:
                last_acc = time.time()
                msg = "(sin movimiento aceptado ~5s)"
                try:
                    lr, sx, sy, ldx, ldy = diag_last
                    if lr is not None:
                        sx_s = f"{float(sx):.2f}" if sx is not None else "?"
                        sy_s = f"{float(sy):.2f}" if sy is not None else "?"
                        msg = (
                            f"{msg} resp_max={diag_resp_max:.3f} last_resp={float(lr):.3f} "
                            f"shift_px=({sx_s},{sy_s}) last_dxdy={ldx},{ldy}"
                        )
                    else:
                        msg = f"{msg} (sin resp)"
                except Exception:
                    pass

                # Debug extra del tracker (modo/marker)
                try:
                    mode_used = str(getattr(tracker, "last_mode_used", "") or "")
                    m = getattr(tracker, "last_marker_px", None)
                    md = getattr(tracker, "last_marker_dpx", (0.0, 0.0))
                    if m is not None:
                        msg = (
                            f"{msg} | mode_used={mode_used or '?'} marker=({float(m[0]):.1f},{float(m[1]):.1f}) "
                            f"dpx=({float(md[0]):.2f},{float(md[1]):.2f})"
                        )
                    else:
                        msg = f"{msg} | mode_used={mode_used or '?'} marker=none"
                except Exception:
                    pass
                print(msg)

                # Guardar un crop de diagnóstico (aunque no haya step aceptado).
                if args.save_crops and crops_saved < int(args.max_crops):
                    try:
                        if last_crop_for_diag is not None and getattr(last_crop_for_diag, "size", 0) != 0:
                            crops_dir.mkdir(parents=True, exist_ok=True)
                            ts = time.time()
                            lr, sx, sy, ldx, ldy = diag_last
                            lr_s = f"{float(lr):.3f}" if lr is not None else "na"
                            p = crops_dir / f"{ts:.6f}_hb_r{lr_s}_sx{sx if sx is not None else 'na'}_sy{sy if sy is not None else 'na'}.png"
                            cv2.imwrite(str(p), last_crop_for_diag)
                            crops_saved += 1
                    except Exception:
                        pass

            elapsed = time.time() - t0
            to_sleep = max(0.0, interval_s - elapsed)
            if to_sleep:
                time.sleep(to_sleep)

    except KeyboardInterrupt:
        print("\ninterrumpido.")
        return 130

    summary = {
        "kind": "summary",
        "ts": time.time(),
        "total_samples": int(total),
        "accepted": int(accepted),
        "accepted_rate": float(accepted) / float(total) if total else 0.0,
        "crops_saved": int(crops_saved),
        "abs_updates": int(abs_updates),
        "net_dx": int(net_dx),
        "net_dy": int(net_dy),
        "abs_last": (
            {"x": int(abs_coords[0]), "y": int(abs_coords[1]), "z": (int(abs_coords[2]) if abs_coords[2] is not None else None)}
            if abs_enabled and abs_coords is not None
            else None
        ),
    }
    _jsonl_append(out_jsonl, summary)

    print(f"Listo. total={total} accepted={accepted} crops={crops_saved} out={out_jsonl}")
    if args.save_crops:
        print(f"crops_dir={crops_dir}")
    if accepted == 0:
        print(
            "Tip: si resp_max queda siempre < min_response, baja MINIMAP_PHASECORR_MIN_RESPONSE (p.ej. 0.08) "
            "o revisa que 'minimap_content' esté bien calibrado y que realmente te estés moviendo durante la captura."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
