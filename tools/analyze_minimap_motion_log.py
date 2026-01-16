from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass
class Sample:
    ts: float
    accepted: bool
    mode_used: str
    tracker_response: float
    diag_resp: float | None
    shift_px: tuple[float, float]
    marker_dpx: tuple[float, float]
    delta_tiles_f: tuple[float, float]


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def _p(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if q <= 0:
        return xs[0]
    if q >= 1:
        return xs[-1]
    i = (len(xs) - 1) * q
    lo = int(math.floor(i))
    hi = int(math.ceil(i))
    if lo == hi:
        return xs[lo]
    frac = i - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _fmt(v: float | None, *, nd: int = 3) -> str:
    if v is None:
        return "na"
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return "na"


def _deadband(v: float, deadband_tiles: float) -> float:
    try:
        if deadband_tiles > 0 and abs(float(v)) < float(deadband_tiles):
            return 0.0
    except Exception:
        return float(v)
    return float(v)


def _emit_from_acc(acc: float, *, threshold_tiles: float) -> int:
    """Match MinimapMotionTracker._emit_step_from_acc behavior."""
    thr = float(threshold_tiles)
    if thr <= 0:
        thr = 0.5

    if acc >= thr:
        return int(math.floor(acc + 0.5))
    if acc <= -thr:
        return -int(math.floor(abs(acc) + 0.5))
    return 0


def _simulate_emits(
    samples: list[Sample],
    *,
    threshold_tiles: float,
    deadband_tiles: float,
    max_step_per_frame: int,
) -> tuple[int, int, int]:
    """Simulate how many emitted steps would occur from logged fractional deltas.

    Returns (accepted_frames, net_dx, net_dy).
    """

    acc_x = 0.0
    acc_y = 0.0
    accepted_frames = 0
    net_dx = 0
    net_dy = 0

    for s in samples:
        dx_f = _deadband(s.delta_tiles_f[0], deadband_tiles)
        dy_f = _deadband(s.delta_tiles_f[1], deadband_tiles)
        acc_x += float(dx_f)
        acc_y += float(dy_f)

        dx = _emit_from_acc(acc_x, threshold_tiles=threshold_tiles)
        dy = _emit_from_acc(acc_y, threshold_tiles=threshold_tiles)

        if dx == 0 and dy == 0:
            continue

        max_step = max(1, int(max_step_per_frame))
        if abs(dx) > max_step:
            dx = int(math.copysign(max_step, dx))
        if abs(dy) > max_step:
            dy = int(math.copysign(max_step, dy))

        # Consume emitted steps (same as tracker).
        acc_x -= float(dx)
        acc_y -= float(dy)

        if dx == 0 and dy == 0:
            continue

        accepted_frames += 1
        net_dx += int(dx)
        net_dy += int(dy)

    return accepted_frames, net_dx, net_dy


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analiza logs JSONL de tools/watch_minimap_motion.py")
    p.add_argument(
        "--in",
        dest="in_path",
        type=str,
        default=str(Path("logs") / "minimap_motion.jsonl"),
        help="Ruta al JSONL.",
    )
    p.add_argument(
        "--simulate",
        action="store_true",
        help="Simula steps usando delta_tiles_f con una grilla de thresholds.",
    )
    p.add_argument(
        "--deadband",
        type=float,
        default=None,
        help="Deadband (tiles). Si no se pasa, usa el meta del log si existe (o 0.03).",
    )
    p.add_argument(
        "--max-step",
        type=int,
        default=None,
        help="Max steps por frame. Si no se pasa, usa el meta del log si existe (o 3).",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    path = Path(args.in_path)
    if not path.exists():
        print(f"No existe: {path}")
        return 2

    meta: dict[str, Any] | None = None
    last_summary: dict[str, Any] | None = None
    samples: list[Sample] = []
    accepted_steps = 0
    motion_events = 0

    for ev in _iter_jsonl(path):
        kind = str(ev.get("kind", "")) if isinstance(ev, dict) else ""

        if kind == "meta":
            # Keep the last meta (file may contain multiple runs).
            meta = ev
            continue

        if kind == "summary":
            last_summary = ev
            continue

        if kind == "minimap_motion":
            motion_events += 1
            accepted_steps += 1
            continue

        if kind == "minimap_sample":
            try:
                tr = ev.get("tracker", {}) or {}
                dg = ev.get("diag", {}) or {}

                ts = float(ev.get("ts", 0.0) or 0.0)
                accepted = bool(ev.get("accepted", False))

                mode_used = str(tr.get("mode_used", "") or "")
                tracker_response = float(tr.get("response", 0.0) or 0.0)

                diag_resp = dg.get("resp", None)
                diag_resp_f = float(diag_resp) if diag_resp is not None else None

                sp = tr.get("shift_px", [0.0, 0.0])
                shift_px = (float(sp[0]), float(sp[1]))

                md = tr.get("marker_dpx", [0.0, 0.0])
                marker_dpx = (float(md[0]), float(md[1]))

                dt = tr.get("delta_tiles_f", [0.0, 0.0])
                delta_tiles_f = (float(dt[0]), float(dt[1]))

                samples.append(
                    Sample(
                        ts=ts,
                        accepted=accepted,
                        mode_used=mode_used,
                        tracker_response=tracker_response,
                        diag_resp=diag_resp_f,
                        shift_px=shift_px,
                        marker_dpx=marker_dpx,
                        delta_tiles_f=delta_tiles_f,
                    )
                )
            except Exception:
                continue

    if not samples:
        # Provide useful output even for older logs.
        print(f"Archivo: {path}")
        if meta:
            cfg = (meta or {}).get("minimap_cfg", {}) if isinstance(meta, dict) else {}
            try:
                print(
                    "Meta: "
                    f"mode={cfg.get('mode', '?')} tile_px={cfg.get('tile_px', '?')} min_resp={cfg.get('min_response', '?')} "
                    f"emit_thr={cfg.get('emit_threshold_tiles', 'na')} deadband={cfg.get('deadband_tiles', 'na')} max_step={cfg.get('max_step_per_frame', '?')}"
                )
            except Exception:
                pass
        if last_summary:
            try:
                print(
                    "Summary: "
                    f"total_samples={last_summary.get('total_samples')} accepted={last_summary.get('accepted')} "
                    f"accepted_rate={last_summary.get('accepted_rate')} crops_saved={last_summary.get('crops_saved')}"
                )
            except Exception:
                pass

        print("\nEste JSONL no tiene eventos 'minimap_sample'.")
        print("- Vuelve a correr el watcher con --log-all para capturar deltas/accumuladores por muestra.")
        print("  Ej: python tools/watch_minimap_motion.py --monitor 2 --seconds 30 --log-all --print")
        return 0

    cfg = (meta or {}).get("minimap_cfg", {}) if isinstance(meta, dict) else {}
    cfg_mode = str(cfg.get("mode", ""))
    cfg_tile_px = cfg.get("tile_px", None)
    cfg_min_resp = cfg.get("min_response", None)
    cfg_emit_thr = cfg.get("emit_threshold_tiles", None)
    cfg_deadband = cfg.get("deadband_tiles", None)
    cfg_max_step = cfg.get("max_step_per_frame", None)

    deadband_tiles = float(args.deadband) if args.deadband is not None else float(cfg_deadband or 0.03)
    max_step = int(args.max_step) if args.max_step is not None else int(cfg_max_step or 3)

    total = len(samples)
    accepted_frames = sum(1 for s in samples if s.accepted)

    modes: dict[str, int] = {}
    for s in samples:
        modes[s.mode_used] = modes.get(s.mode_used, 0) + 1

    tr_resp = [s.tracker_response for s in samples]
    diag_resp_vals = [s.diag_resp for s in samples if s.diag_resp is not None]

    def mag2(a: tuple[float, float]) -> float:
        return abs(float(a[0])) + abs(float(a[1]))

    shift_mag = [mag2(s.shift_px) for s in samples]
    marker_mag = [mag2(s.marker_dpx) for s in samples]
    delta_mag = [mag2(s.delta_tiles_f) for s in samples]

    print(f"Archivo: {path}")
    if meta:
        try:
            print(
                "Meta: "
                f"mode={cfg_mode or '?'} tile_px={cfg_tile_px} min_resp={cfg_min_resp} "
                f"emit_thr={cfg_emit_thr} deadband={cfg_deadband} max_step={cfg_max_step}"
            )
        except Exception:
            pass

    print(f"Muestras: {total} | accepted_frames={accepted_frames} ({(accepted_frames/total*100.0 if total else 0.0):.1f}%)")
    if motion_events:
        print(f"Eventos minimap_motion (steps): {motion_events}")

    if modes:
        modes_s = ", ".join(f"{k or '?'}={v}" for k, v in sorted(modes.items(), key=lambda kv: (-kv[1], kv[0])))
        print(f"Modo usado (tracker): {modes_s}")

    print(
        "Resp (tracker): "
        f"p50={_fmt(_p(tr_resp, 0.5))} p90={_fmt(_p(tr_resp, 0.9))} max={_fmt(_p(tr_resp, 1.0))}"
    )
    if diag_resp_vals:
        print(
            "Resp (diag phaseCorr): "
            f"p50={_fmt(_p(diag_resp_vals, 0.5))} p90={_fmt(_p(diag_resp_vals, 0.9))} max={_fmt(_p(diag_resp_vals, 1.0))}"
        )

    print(
        "Magnitudes (|x|+|y|): "
        f"shift_px p50={_fmt(_p(shift_mag, 0.5), nd=2)} p90={_fmt(_p(shift_mag, 0.9), nd=2)} | "
        f"marker_dpx p50={_fmt(_p(marker_mag, 0.5), nd=2)} p90={_fmt(_p(marker_mag, 0.9), nd=2)} | "
        f"delta_tiles_f p50={_fmt(_p(delta_mag, 0.5))} p90={_fmt(_p(delta_mag, 0.9))}"
    )

    # Simple signal sanity: how much movement survives deadband
    delta_mag_db = [abs(_deadband(s.delta_tiles_f[0], deadband_tiles)) + abs(_deadband(s.delta_tiles_f[1], deadband_tiles)) for s in samples]
    nonzero_db = sum(1 for v in delta_mag_db if v > 0)
    print(
        f"Deadband usado={deadband_tiles:.3f} tiles -> muestras con delta!=0: {nonzero_db}/{total} ({(nonzero_db/total*100.0 if total else 0.0):.1f}%)"
    )

    if args.simulate and samples:
        print("\nSimulación (desde delta_tiles_f):")
        for thr in [0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60]:
            acc_n, net_dx, net_dy = _simulate_emits(
                samples,
                threshold_tiles=float(thr),
                deadband_tiles=float(deadband_tiles),
                max_step_per_frame=int(max_step),
            )
            rate = (acc_n / total) if total else 0.0
            print(
                f"  thr={thr:.2f} -> accepted_frames={acc_n} ({rate*100.0:.1f}%) net_dx={net_dx} net_dy={net_dy}"
            )

    # Basic suggestion heuristics
    print("\nSugerencia rápida:")
    try:
        if _p(delta_mag_db, 0.9) is not None and float(_p(delta_mag_db, 0.9) or 0.0) < 0.10:
            print("- Señal muy chica: baja emit_thr (0.25–0.35) o reduce deadband; revisa ROI minimap_content.")
        else:
            print("- Señal OK: ajusta emit_thr para que emita 1 step por ~tile (0.30–0.50 típico).")

        if modes.get("marker", 0) == 0 and modes.get("scroll", 0) > 0:
            print("- Estás usando scroll: prueba MINIMAP_MODE=marker (muchas UIs mueven el marcador y no el mapa).")
        if modes.get("marker", 0) > 0 and _p(marker_mag, 0.9) is not None and float(_p(marker_mag, 0.9) or 0.0) < 0.8:
            print("- Marker casi no se mueve: puede que el detector esté agarrando un pixel estático (blanco). Ajusta MARKER_* o ROI.")
    except Exception:
        print("- Revisa delta_tiles_f y marker_dpx para tunear umbrales.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
