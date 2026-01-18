from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def list_mss_monitors() -> list[dict]:
    """Lista monitores según MSS (idx 0..n-1)."""

    try:
        from mss import mss

        with mss() as sct:
            return list(getattr(sct, "monitors", []) or [])
    except Exception:
        return []


def print_mss_monitors_table(*, selected: int | None = None) -> None:
    mons = list_mss_monitors()
    n = len(mons)
    print(f"🖥️  MSS monitors detectados: {n} (válidos: 0..{max(0, n - 1)})")
    for i, m in enumerate(mons):
        try:
            left = int(m.get("left", 0))
            top = int(m.get("top", 0))
            width = int(m.get("width", 0))
            height = int(m.get("height", 0))
            mark = "*" if (selected is not None and int(selected) == int(i)) else " "
            print(f" {mark} idx={i} {width}x{height} bounds=({left},{top},{left + width},{top + height})")
        except Exception:
            continue


def _short_title(t: str, n: int = 42) -> str:
    s = str(t or "").strip().replace("\n", " ")
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."


def format_capture_status_line(
    *,
    backend: str,
    window_title: str,
    monitor_idx: int | None,
    mean: float | None,
    black_streak: int | None,
) -> str:
    m_s = "?" if monitor_idx is None else str(int(monitor_idx))
    mean_s = "?" if mean is None else f"{float(mean):.2f}"
    bs_s = "?" if black_streak is None else str(int(black_streak))
    return f"backend={backend} title='{_short_title(window_title)}' mon={m_s} mean={mean_s} black_streak={bs_s}"


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _load_roi_config(resolution: tuple[int, int]) -> tuple[dict, list[int]]:
    width, height = resolution
    config_files = {
        (2048, 1076): "configs/rois_guess.json",
        (1920, 1080): "configs/rois_guess_1920x1080.json",
        (1920, 1009): "configs/rois_guess_1920x1080.json",
    }
    config_file = config_files.get((width, height), "configs/rois_guess_1920x1080.json")
    with open(config_file, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg["rois_guess_norm"], cfg["source_resolution"]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live smoke test: read HP/MP real + simulate other states.")
    p.add_argument(
        "--monitor",
        type=int,
        default=None,
        help="Índice MSS del monitor (0..n-1). Si se omite, usa FORCE_MONITOR si está seteado.",
    )
    p.add_argument("--seconds", type=float, default=15.0)
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--paralyzed", action="store_true")
    p.add_argument("--haste", action="store_true")
    p.add_argument("--utamo", action="store_true")
    p.add_argument("--hungry", action="store_true")
    return p.parse_args()


def main() -> int:
    _add_src_to_syspath()

    from capture.dxgi_capture import DXGICapture
    from gamestate.builder import GameStateBuilder
    from runtime_config import HealingConfig, SimulationConfig
    from decision.signals import evaluate_signals

    args = _parse_args()

    forced_env = (os.getenv("FORCE_MONITOR", "") or "").strip()
    monitor_idx = args.monitor
    if monitor_idx is None and forced_env:
        try:
            monitor_idx = int(float(forced_env))
        except Exception:
            monitor_idx = None

    # Tabla de monitores MSS (ONE-SHOT).
    try:
        print_mss_monitors_table(selected=monitor_idx)
    except Exception:
        pass

    cap = DXGICapture(force_monitor=monitor_idx)
    builder = GameStateBuilder()

    sim = SimulationConfig(
        enabled=True,
        paralyzed=bool(args.paralyzed),
        haste_active=bool(args.haste),
        utamo_active=bool(args.utamo),
        hungry=bool(args.hungry),
    )
    healing = HealingConfig(enabled=False)

    t_end = time.time() + float(args.seconds)
    period = 1.0 / max(1.0, float(args.fps))

    rois = None
    resolution = None

    last_print = 0.0
    print(f"▶ Smoke live HP/MP. monitor(MSS)={monitor_idx} seconds={args.seconds} fps={args.fps}")
    print(f"   simulated: paralyzed={sim.paralyzed} haste={sim.haste_active} utamo={sim.utamo_active} hungry={sim.hungry}")

    while time.time() < t_end:
        t0 = time.time()
        frame = cap.capture()
        if frame is None:
            time.sleep(0.05)
            continue

        if rois is None or resolution is None:
            resolution = (int(frame.shape[1]), int(frame.shape[0]))
            rois_loaded, source_resolution = _load_roi_config(resolution)
            rois_loaded["_source_resolution"] = source_resolution
            rois = rois_loaded
            print(f"🧭 ROIs loaded for {resolution[0]}x{resolution[1]} (source={source_resolution})")

        gs = builder.update_from_frame(frame, rois, resolution)
        sig = evaluate_signals(gs, healing, sim)

        now = time.time()
        if now - last_print >= 0.5:
            last_print = now
            hp = f"{sig.hp_current}/{sig.hp_max}" if sig.hp_current is not None and sig.hp_max is not None else "?"
            mp = f"{sig.mp_current}/{sig.mp_max}" if sig.mp_current is not None and sig.mp_max is not None else "?"
            hp_pct = f"{sig.hp_pct:.1f}%" if sig.hp_pct is not None else "?"
            mp_pct = f"{sig.mp_pct:.1f}%" if sig.mp_pct is not None else "?"

            try:
                backend = str(getattr(cap, "capture_backend", "") or "")
            except Exception:
                backend = ""
            if not backend:
                try:
                    backend = str(getattr(cap, "capture_state", "") or "")
                except Exception:
                    backend = ""
            if not backend:
                backend = "?"

            try:
                title = str(getattr(cap, "client_title", "") or "")
            except Exception:
                title = ""
            try:
                mon = getattr(cap, "capture_monitor_index", None)
                mon_i = int(mon) if mon is not None else None
            except Exception:
                mon_i = None
            try:
                mean_v = getattr(cap, "last_frame_mean", None)
                mean_f = float(mean_v) if mean_v is not None else None
            except Exception:
                mean_f = None
            try:
                bs = int(getattr(cap, "black_streak", 0) or 0)
            except Exception:
                bs = 0

            flags = []
            if sig.paralyzed:
                flags.append("paralyzed")
            if sig.haste_active:
                flags.append("haste")
            if sig.utamo_active:
                flags.append("utamo")
            if sig.hungry:
                flags.append("hungry")
            fstr = ",".join(flags) if flags else "-"
            cap_s = format_capture_status_line(
                backend=backend,
                window_title=title,
                monitor_idx=mon_i,
                mean=mean_f,
                black_streak=bs,
            )
            print(f"{cap_s} | HP {hp} ({hp_pct}) | MP {mp} ({mp_pct}) | flags={fstr}")

        elapsed = time.time() - t0
        to_sleep = max(0.0, period - elapsed)
        if to_sleep:
            time.sleep(to_sleep)

    print("✅ Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
