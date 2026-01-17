from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def _bool_env(name: str) -> bool:
    try:
        return (os.getenv(name, "") or "").strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return False


def _safe_get(d: Any, *keys: str, default=None):
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def _fmt_num(x: Any) -> str:
    try:
        if x is None:
            return "-"
        if isinstance(x, bool):
            return str(int(x))
        if isinstance(x, int):
            return str(x)
        if isinstance(x, float):
            return f"{x:.2f}"
        s = str(x)
        return s
    except Exception:
        return "?"


def _find_json_files(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return sorted([p for p in base.glob("*.json") if p.is_file()])


def _top_k(d: dict[str, int], k: int = 10) -> list[tuple[str, int]]:
    try:
        return sorted(d.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))[: int(k)]
    except Exception:
        return []


def _inc(d: dict[str, int], key: Any) -> None:
    try:
        k = str(key if key is not None else "-")
    except Exception:
        k = "?"
    d[k] = int(d.get(k, 0)) + 1


def _hpmp_state(cur: Any, mx: Any, pct: Any) -> str:
    try:
        if cur is not None and mx is not None:
            return "cur/max"
        if pct is not None:
            return "pct"
        return "missing"
    except Exception:
        return "missing"


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect replay JSON snapshots focusing on HP/MP fields.")
    ap.add_argument(
        "--dir",
        default=os.getenv("REPLAY_OUT_DIR", "logs/replay") or "logs/replay",
        help="Replay directory containing *.json (default: REPLAY_OUT_DIR or logs/replay)",
    )
    ap.add_argument("--tail", type=int, default=10, help="How many newest JSON files to print")
    ap.add_argument("--limit", type=int, default=0, help="Max files to scan (0 = all)")
    ap.add_argument(
        "--summary",
        action="store_true",
        help="Print a concise summary of HP/MP extraction modes and reasons",
    )
    ap.add_argument("--show-rois", action="store_true", help="Check if ROI PNGs exist for each timestamp")
    args = ap.parse_args()

    base = Path(args.dir)
    # Backwards-compatible default: if the requested dir doesn't exist, try the older default.
    if not base.exists() and str(args.dir) in {"logs/replay", os.getenv("REPLAY_OUT_DIR", "logs/replay") or "logs/replay"}:
        legacy = Path("logs/replay_hpmp_check")
        if legacy.exists():
            base = legacy
    files = _find_json_files(base)
    print(f"dir={base} json_files={len(files)}")
    if not files:
        return 2

    if args.limit and int(args.limit) > 0:
        files = files[-int(args.limit) :]

    # Summary over the selected set.
    if args.summary:
        hp_methods: dict[str, int] = {}
        hp_reasons: dict[str, int] = {}
        hp_bar_reasons: dict[str, int] = {}
        hp_bar_roi_reasons: dict[str, int] = {}
        hp_states: dict[str, int] = {}

        mp_methods: dict[str, int] = {}
        mp_reasons: dict[str, int] = {}
        mp_bar_reasons: dict[str, int] = {}
        mp_bar_roi_reasons: dict[str, int] = {}
        mp_states: dict[str, int] = {}

        vg_active = 0
        vg_total = 0
        vg_reasons: dict[str, int] = {}

        for p in files:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue

            tel = _safe_get(data, "telemetry", default={}) or {}
            gs = _safe_get(data, "gamestate", default={}) or {}

            hp_cur = _safe_get(tel, "hp_current", default=_safe_get(gs, "hp_current"))
            hp_max = _safe_get(tel, "hp_max", default=_safe_get(gs, "hp_max"))
            hp_pct = _safe_get(tel, "hp_pct", default=_safe_get(gs, "hp_pct"))
            mp_cur = _safe_get(tel, "mp_current", default=_safe_get(gs, "mp_current"))
            mp_max = _safe_get(tel, "mp_max", default=_safe_get(gs, "mp_max"))
            mp_pct = _safe_get(tel, "mp_pct", default=_safe_get(gs, "mp_pct"))

            # Prefer deep hud_debug when present (REPLAY_DEBUG_HPMP=1).
            hd_hp = _safe_get(tel, "hud_debug_hp")
            hd_mp = _safe_get(tel, "hud_debug_mp")

            if isinstance(hd_hp, dict):
                _inc(hp_methods, _safe_get(hd_hp, "ocr_method"))
                _inc(hp_reasons, _safe_get(hd_hp, "ocr_reason"))
                _inc(hp_bar_reasons, _safe_get(hd_hp, "bar_reason"))
                _inc(hp_bar_roi_reasons, _safe_get(hd_hp, "bar_roi_reason"))
            else:
                _inc(hp_methods, _safe_get(tel, "hp_method"))
                _inc(hp_reasons, _safe_get(tel, "hp_reason"))

            if isinstance(hd_mp, dict):
                _inc(mp_methods, _safe_get(hd_mp, "ocr_method"))
                _inc(mp_reasons, _safe_get(hd_mp, "ocr_reason"))
                _inc(mp_bar_reasons, _safe_get(hd_mp, "bar_reason"))
                _inc(mp_bar_roi_reasons, _safe_get(hd_mp, "bar_roi_reason"))
            else:
                _inc(mp_methods, _safe_get(tel, "mp_method"))
                _inc(mp_reasons, _safe_get(tel, "mp_reason"))

            _inc(hp_states, _hpmp_state(hp_cur, hp_max, hp_pct))
            _inc(mp_states, _hpmp_state(mp_cur, mp_max, mp_pct))

            vg = _safe_get(tel, "vision_guard")
            if isinstance(vg, dict):
                vg_total += 1
                if bool(vg.get("active")):
                    vg_active += 1
                _inc(vg_reasons, vg.get("reason"))

        print("\nSUMMARY")
        print("  HP state:", ", ".join(f"{k}={v}" for k, v in _top_k(hp_states, 10)))
        print("  MP state:", ", ".join(f"{k}={v}" for k, v in _top_k(mp_states, 10)))
        print("  HP methods:", ", ".join(f"{k}={v}" for k, v in _top_k(hp_methods, 8)))
        print("  HP reasons:", ", ".join(f"{k}={v}" for k, v in _top_k(hp_reasons, 8)))
        if hp_bar_reasons:
            print("  HP bar_reason:", ", ".join(f"{k}={v}" for k, v in _top_k(hp_bar_reasons, 8)))
        if hp_bar_roi_reasons:
            print("  HP bar_roi_reason:", ", ".join(f"{k}={v}" for k, v in _top_k(hp_bar_roi_reasons, 8)))
        print("  MP methods:", ", ".join(f"{k}={v}" for k, v in _top_k(mp_methods, 8)))
        print("  MP reasons:", ", ".join(f"{k}={v}" for k, v in _top_k(mp_reasons, 8)))
        if mp_bar_reasons:
            print("  MP bar_reason:", ", ".join(f"{k}={v}" for k, v in _top_k(mp_bar_reasons, 8)))
        if mp_bar_roi_reasons:
            print("  MP bar_roi_reason:", ", ".join(f"{k}={v}" for k, v in _top_k(mp_bar_roi_reasons, 8)))
        if vg_total:
            print(f"  VisionGuard active: {vg_active}/{vg_total}")
            print("  VisionGuard reasons:", ", ".join(f"{k}={v}" for k, v in _top_k(vg_reasons, 8)))
        print("")

    tail = max(1, int(args.tail))
    for p in files[-tail:]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"{p.name}: ERROR reading JSON: {e}")
            continue

        tel = _safe_get(data, "telemetry", default={}) or {}
        gs = _safe_get(data, "gamestate", default={}) or {}

        hp_cur = _safe_get(tel, "hp_current", default=_safe_get(gs, "hp_current"))
        hp_max = _safe_get(tel, "hp_max", default=_safe_get(gs, "hp_max"))
        hp_pct = _safe_get(tel, "hp_pct", default=_safe_get(gs, "hp_pct"))
        hp_method = _safe_get(tel, "hp_method")
        hp_reason = _safe_get(tel, "hp_reason")

        mp_cur = _safe_get(tel, "mp_current", default=_safe_get(gs, "mp_current"))
        mp_max = _safe_get(tel, "mp_max", default=_safe_get(gs, "mp_max"))
        mp_pct = _safe_get(tel, "mp_pct", default=_safe_get(gs, "mp_pct"))
        mp_method = _safe_get(tel, "mp_method")
        mp_reason = _safe_get(tel, "mp_reason")

        # Deep HUD debug (only present when REPLAY_DEBUG_HPMP=1)
        hud_hp = _safe_get(tel, "hud_debug_hp")
        hud_mp = _safe_get(tel, "hud_debug_mp")
        hud_rois_px = _safe_get(tel, "hud_rois_px")
        vision_guard = _safe_get(tel, "vision_guard")

        roi_offset = _safe_get(data, "rois_state", "roi_offset_px")
        roi_score = _safe_get(data, "rois_state", "roi_offset_score")

        print(p.name)
        print(
            "  HP",
            f"{_fmt_num(hp_cur)}/{_fmt_num(hp_max)}",
            f"pct={_fmt_num(hp_pct)}",
            f"method={_fmt_num(hp_method)}",
            f"reason={_fmt_num(hp_reason)}",
        )
        if isinstance(hud_hp, dict):
            print(
                "  HP hud_debug",
                f"ocr_method={_fmt_num(_safe_get(hud_hp, 'ocr_method'))}",
                f"ocr_reason={_fmt_num(_safe_get(hud_hp, 'ocr_reason'))}",
                f"bar_ratio={_fmt_num(_safe_get(hud_hp, 'bar_ratio'))}",
                f"bar_reason={_fmt_num(_safe_get(hud_hp, 'bar_reason'))}",
                f"bar_roi_reason={_fmt_num(_safe_get(hud_hp, 'bar_roi_reason'))}",
            )
        print(
            "  MP",
            f"{_fmt_num(mp_cur)}/{_fmt_num(mp_max)}",
            f"pct={_fmt_num(mp_pct)}",
            f"method={_fmt_num(mp_method)}",
            f"reason={_fmt_num(mp_reason)}",
        )
        if isinstance(hud_mp, dict):
            print(
                "  MP hud_debug",
                f"ocr_method={_fmt_num(_safe_get(hud_mp, 'ocr_method'))}",
                f"ocr_reason={_fmt_num(_safe_get(hud_mp, 'ocr_reason'))}",
                f"bar_ratio={_fmt_num(_safe_get(hud_mp, 'bar_ratio'))}",
                f"bar_reason={_fmt_num(_safe_get(hud_mp, 'bar_reason'))}",
                f"bar_roi_reason={_fmt_num(_safe_get(hud_mp, 'bar_roi_reason'))}",
            )

        if isinstance(vision_guard, dict):
            try:
                print(
                    "  VisionGuard",
                    f"active={_fmt_num(vision_guard.get('active'))}",
                    f"reason={_fmt_num(vision_guard.get('reason'))}",
                )
            except Exception:
                pass

        if isinstance(hud_rois_px, dict) and _bool_env("REPLAY_DEBUG_HPMP"):
            # Show only a focused subset (if present) to avoid noisy logs.
            try:
                keys = ["hp_top_ocr", "mp_top_ocr", "hp_low_bar", "mp_low_bar", "hpmp_top_strip", "states_icons"]
                picked = {k: hud_rois_px.get(k) for k in keys if k in hud_rois_px}
                if picked:
                    print(f"  ROIs px: {picked}")
            except Exception:
                pass
        if roi_offset is not None or roi_score is not None:
            print(f"  ROI offset px={roi_offset} score={_fmt_num(roi_score)}")

        if args.show_rois:
            ts = None
            try:
                ts = float(p.stem)
            except Exception:
                ts = None
            rois_dir = base / "rois"
            if ts is None or not rois_dir.exists():
                print("  ROIs: (no timestamp or rois/ missing)")
            else:
                prefix = f"{ts:.6f}_"
                imgs = sorted([q for q in rois_dir.glob(f"{prefix}*.png") if q.is_file()])
                print(f"  ROIs: {len(imgs)} png")
                # Show a small sample
                for q in imgs[:6]:
                    try:
                        print(f"    {q.name} ({q.stat().st_size} bytes)")
                    except Exception:
                        print(f"    {q.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
