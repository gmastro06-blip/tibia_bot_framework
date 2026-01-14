from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


def iter_jsonl_events(lines: Iterable[str]) -> Iterator[Dict[str, Any]]:
    for raw in lines:
        s = (raw or "").strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if isinstance(obj, dict):
            yield obj


def _pick_ts(e: Dict[str, Any]) -> Optional[float]:
    for k in ("ts", "timestamp", "time"):
        if k in e:
            try:
                return float(e[k])
            except Exception:
                continue
    return None


def _p(values: list[float], q: float) -> float | None:
    """Percentile with linear interpolation (same as tools/analyze_minimap_motion_log.py)."""

    if not values:
        return None
    xs = sorted(values)
    if q <= 0:
        return xs[0]
    if q >= 1:
        return xs[-1]
    i = (len(xs) - 1) * float(q)
    lo = int(math.floor(i))
    hi = int(math.ceil(i))
    if lo == hi:
        return xs[lo]
    frac = i - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


@dataclass
class KPIs:
    window_s: float | None
    telemetry_fps: float | None
    capture_fps: float | None
    vision_fps: float | None
    decision_fps: float | None
    effective_fps: float | None
    n_telemetry: int
    n_health: int
    avg_capture_ms: float | None
    avg_vision_ms: float | None
    avg_decision_ms: float | None
    avg_end_to_end_ms: float | None

    p50_capture_ms: float | None
    p95_capture_ms: float | None
    p50_vision_ms: float | None
    p95_vision_ms: float | None
    p50_decision_ms: float | None
    p95_decision_ms: float | None
    p50_end_to_end_ms: float | None
    p95_end_to_end_ms: float | None

    drops_frame_oldest: int | None
    drops_frame_new: int | None
    drops_gs_oldest: int | None
    drops_gs_new: int | None
    drops_replay_oldest: int | None
    drops_replay_new: int | None
    drops_jsonl_oldest: int | None
    drops_jsonl_new: int | None

    drop_frame_oldest_rate_s: float | None
    drop_frame_new_rate_s: float | None
    drop_gs_oldest_rate_s: float | None
    drop_gs_new_rate_s: float | None
    drop_replay_oldest_rate_s: float | None
    drop_replay_new_rate_s: float | None
    drop_jsonl_oldest_rate_s: float | None
    drop_jsonl_new_rate_s: float | None

    stale_gamestate_events: int | None
    stale_gamestate_ratio: float | None
    stale_gamestate_rate_min: float | None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_s": self.window_s,
            "telemetry_fps": self.telemetry_fps,
            "capture_fps": self.capture_fps,
            "vision_fps": self.vision_fps,
            "decision_fps": self.decision_fps,
            "effective_fps": self.effective_fps,
            "n_telemetry": self.n_telemetry,
            "n_health": self.n_health,
            "avg_capture_ms": self.avg_capture_ms,
            "avg_vision_ms": self.avg_vision_ms,
            "avg_decision_ms": self.avg_decision_ms,
            "avg_end_to_end_ms": self.avg_end_to_end_ms,
            "p50_capture_ms": self.p50_capture_ms,
            "p95_capture_ms": self.p95_capture_ms,
            "p50_vision_ms": self.p50_vision_ms,
            "p95_vision_ms": self.p95_vision_ms,
            "p50_decision_ms": self.p50_decision_ms,
            "p95_decision_ms": self.p95_decision_ms,
            "p50_end_to_end_ms": self.p50_end_to_end_ms,
            "p95_end_to_end_ms": self.p95_end_to_end_ms,
            "drops_frame_oldest": self.drops_frame_oldest,
            "drops_frame_new": self.drops_frame_new,
            "drops_gs_oldest": self.drops_gs_oldest,
            "drops_gs_new": self.drops_gs_new,
            "drops_replay_oldest": self.drops_replay_oldest,
            "drops_replay_new": self.drops_replay_new,
            "drops_jsonl_oldest": self.drops_jsonl_oldest,
            "drops_jsonl_new": self.drops_jsonl_new,
            "drop_frame_oldest_rate_s": self.drop_frame_oldest_rate_s,
            "drop_frame_new_rate_s": self.drop_frame_new_rate_s,
            "drop_gs_oldest_rate_s": self.drop_gs_oldest_rate_s,
            "drop_gs_new_rate_s": self.drop_gs_new_rate_s,
            "drop_replay_oldest_rate_s": self.drop_replay_oldest_rate_s,
            "drop_replay_new_rate_s": self.drop_replay_new_rate_s,
            "drop_jsonl_oldest_rate_s": self.drop_jsonl_oldest_rate_s,
            "drop_jsonl_new_rate_s": self.drop_jsonl_new_rate_s,
            "stale_gamestate_events": self.stale_gamestate_events,
            "stale_gamestate_ratio": self.stale_gamestate_ratio,
            "stale_gamestate_rate_min": self.stale_gamestate_rate_min,
        }


def compute_kpis(events: List[Dict[str, Any]], *, last_seconds: float | None = None) -> KPIs:
    # Sort by timestamp when available.
    ev_with_ts: List[Tuple[float, Dict[str, Any]]] = []
    ev_no_ts: List[Dict[str, Any]] = []
    for e in events:
        ts = _pick_ts(e)
        if ts is None:
            ev_no_ts.append(e)
        else:
            ev_with_ts.append((ts, e))
    ev_with_ts.sort(key=lambda t: t[0])

    if last_seconds is not None and ev_with_ts:
        end = ev_with_ts[-1][0]
        start = end - float(last_seconds)
        ev_with_ts = [(ts, e) for ts, e in ev_with_ts if ts >= start]

    # Pull back into plain list in time order.
    ordered: List[Dict[str, Any]] = [e for _ts, e in ev_with_ts] + ev_no_ts

    tel = [e for e in ordered if str(e.get("kind", "")).lower() == "telemetry"]
    health = [e for e in ordered if str(e.get("kind", "")).lower() == "health"]

    window_s: float | None = None
    telemetry_fps: float | None = None
    health_window_s: float | None = None
    if len(tel) >= 2:
        t0 = _pick_ts(tel[0])
        t1 = _pick_ts(tel[-1])
        if t0 is not None and t1 is not None and t1 > t0:
            window_s = float(t1 - t0)
            telemetry_fps = float(len(tel) / window_s) if window_s > 0 else None

    if len(health) >= 2:
        h0 = _pick_ts(health[0])
        h1 = _pick_ts(health[-1])
        if h0 is not None and h1 is not None and h1 > h0:
            health_window_s = float(h1 - h0)

    window_for_rates = health_window_s if health_window_s is not None else window_s

    def avg_key(es: List[Dict[str, Any]], key: str) -> float | None:
        vals: List[float] = []
        for e in es:
            if key in e:
                try:
                    vals.append(float(e[key]))
                except Exception:
                    continue
        return (sum(vals) / len(vals)) if vals else None

    avg_capture_ms = avg_key(health, "capture_ms_last")
    avg_vision_ms = avg_key(health, "vision_ms_last")
    avg_decision_ms = avg_key(health, "decision_ms_last")

    def series_key(es: List[Dict[str, Any]], key: str) -> list[float]:
        out: list[float] = []
        for e in es:
            if key in e:
                try:
                    out.append(float(e[key]))
                except Exception:
                    continue
        return out

    cap_ms = series_key(health, "capture_ms_last")
    vis_ms = series_key(health, "vision_ms_last")
    dec_ms = series_key(health, "decision_ms_last")

    end_to_end_ms: list[float] = []
    for e in health:
        try:
            if "capture_ms_last" not in e or "vision_ms_last" not in e or "decision_ms_last" not in e:
                continue
            c = float(e["capture_ms_last"])
            v = float(e["vision_ms_last"])
            d = float(e["decision_ms_last"])
            end_to_end_ms.append(float(c + v + d))
        except Exception:
            continue

    avg_end_to_end_ms = (sum(end_to_end_ms) / len(end_to_end_ms)) if end_to_end_ms else None

    p50_capture_ms = _p(cap_ms, 0.50)
    p95_capture_ms = _p(cap_ms, 0.95)
    p50_vision_ms = _p(vis_ms, 0.50)
    p95_vision_ms = _p(vis_ms, 0.95)
    p50_decision_ms = _p(dec_ms, 0.50)
    p95_decision_ms = _p(dec_ms, 0.95)
    p50_end_to_end_ms = _p(end_to_end_ms, 0.50)
    p95_end_to_end_ms = _p(end_to_end_ms, 0.95)

    def counter_delta(es: List[Dict[str, Any]], key: str) -> int | None:
        vals: List[int] = []
        for e in es:
            if key in e:
                try:
                    vals.append(int(e[key]))
                except Exception:
                    continue
        if len(vals) < 2:
            return None
        return int(vals[-1] - vals[0])

    drops_frame_oldest = counter_delta(health, "drop_frame_queue")
    drops_gs_oldest = counter_delta(health, "drop_gs_queue")
    drops_jsonl_oldest = counter_delta(health, "drop_jsonl_queue")

    drops_frame_new = counter_delta(health, "drop_frame_queue_new")
    drops_gs_new = counter_delta(health, "drop_gs_queue_new")
    drops_replay_oldest = counter_delta(health, "drop_replay_queue")
    drops_replay_new = counter_delta(health, "drop_replay_queue_new")
    drops_jsonl_new = counter_delta(health, "drop_jsonl_queue_new")

    def rate(delta: int | None, win: float | None) -> float | None:
        try:
            if delta is None or win is None or float(win) <= 0:
                return None
            return float(delta) / float(win)
        except Exception:
            return None

    drop_frame_oldest_rate_s = rate(drops_frame_oldest, window_for_rates)
    drop_frame_new_rate_s = rate(drops_frame_new, window_for_rates)
    drop_gs_oldest_rate_s = rate(drops_gs_oldest, window_for_rates)
    drop_gs_new_rate_s = rate(drops_gs_new, window_for_rates)
    drop_replay_oldest_rate_s = rate(drops_replay_oldest, window_for_rates)
    drop_replay_new_rate_s = rate(drops_replay_new, window_for_rates)
    drop_jsonl_oldest_rate_s = rate(drops_jsonl_oldest, window_for_rates)
    drop_jsonl_new_rate_s = rate(drops_jsonl_new, window_for_rates)

    def counter_fps(es: List[Dict[str, Any]], key: str, win: float | None) -> float | None:
        d = counter_delta(es, key)
        if d is None:
            return None
        return rate(d, win)

    capture_fps = counter_fps(health, "capture_ok", health_window_s)
    vision_fps = counter_fps(health, "vision_ok", health_window_s)
    decision_fps = counter_fps(health, "decision_ok", health_window_s)

    eff_candidates = [v for v in [decision_fps, vision_fps, capture_fps] if v is not None]
    effective_fps = min(eff_candidates) if eff_candidates else telemetry_fps

    # Stale GameState rate (if we have health snapshots)
    stale_gamestate_events: int | None = None
    stale_gamestate_ratio: float | None = None
    stale_gamestate_rate_min: float | None = None
    if health:
        try:
            stale_thr = float(os.getenv("WATCHDOG_STALE_GS_S", "5").strip() or "5")
        except Exception:
            stale_thr = 5.0

        n_stale = 0
        for e in health:
            try:
                warn = str(e.get("warn", "") or "")
                if "stale_gs" in warn:
                    n_stale += 1
                    continue
            except Exception:
                pass
            try:
                gs_age = e.get("gs_age_s", None)
                if gs_age is not None and float(gs_age) >= float(stale_thr):
                    n_stale += 1
            except Exception:
                pass

        stale_gamestate_events = int(n_stale)
        stale_gamestate_ratio = (float(n_stale) / float(len(health))) if health else None
        try:
            if health_window_s is not None and health_window_s > 0:
                stale_gamestate_rate_min = (float(n_stale) / float(health_window_s)) * 60.0
        except Exception:
            stale_gamestate_rate_min = None

    return KPIs(
        window_s=window_s,
        telemetry_fps=telemetry_fps,
        capture_fps=capture_fps,
        vision_fps=vision_fps,
        decision_fps=decision_fps,
        effective_fps=effective_fps,
        n_telemetry=len(tel),
        n_health=len(health),
        avg_capture_ms=avg_capture_ms,
        avg_vision_ms=avg_vision_ms,
        avg_decision_ms=avg_decision_ms,
        avg_end_to_end_ms=avg_end_to_end_ms,
        p50_capture_ms=p50_capture_ms,
        p95_capture_ms=p95_capture_ms,
        p50_vision_ms=p50_vision_ms,
        p95_vision_ms=p95_vision_ms,
        p50_decision_ms=p50_decision_ms,
        p95_decision_ms=p95_decision_ms,
        p50_end_to_end_ms=p50_end_to_end_ms,
        p95_end_to_end_ms=p95_end_to_end_ms,
        drops_frame_oldest=drops_frame_oldest,
        drops_frame_new=drops_frame_new,
        drops_gs_oldest=drops_gs_oldest,
        drops_gs_new=drops_gs_new,
        drops_replay_oldest=drops_replay_oldest,
        drops_replay_new=drops_replay_new,
        drops_jsonl_oldest=drops_jsonl_oldest,
        drops_jsonl_new=drops_jsonl_new,
        drop_frame_oldest_rate_s=drop_frame_oldest_rate_s,
        drop_frame_new_rate_s=drop_frame_new_rate_s,
        drop_gs_oldest_rate_s=drop_gs_oldest_rate_s,
        drop_gs_new_rate_s=drop_gs_new_rate_s,
        drop_replay_oldest_rate_s=drop_replay_oldest_rate_s,
        drop_replay_new_rate_s=drop_replay_new_rate_s,
        drop_jsonl_oldest_rate_s=drop_jsonl_oldest_rate_s,
        drop_jsonl_new_rate_s=drop_jsonl_new_rate_s,
        stale_gamestate_events=stale_gamestate_events,
        stale_gamestate_ratio=stale_gamestate_ratio,
        stale_gamestate_rate_min=stale_gamestate_rate_min,
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Report basic KPIs from telemetry JSONL")
    ap.add_argument("path", nargs="?", default="logs/telemetry.jsonl")
    ap.add_argument("--last-seconds", type=float, default=0.0)
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ns = ap.parse_args(argv)

    p = Path(ns.path)
    if not p.is_file():
        raise SystemExit(f"File not found: {p}")

    events = list(iter_jsonl_events(p.read_text(encoding="utf-8", errors="ignore").splitlines()))
    kpis = compute_kpis(events, last_seconds=(ns.last_seconds if ns.last_seconds > 0 else None))

    if ns.format == "json":
        print(json.dumps(kpis.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(f"events: telemetry={kpis.n_telemetry} health={kpis.n_health}")
    if kpis.window_s is not None and kpis.telemetry_fps is not None:
        print(f"telemetry_fps: {kpis.telemetry_fps:.2f} over {kpis.window_s:.1f}s")
    else:
        print("telemetry_fps: n/a")

    if kpis.effective_fps is not None:
        parts = []
        if kpis.capture_fps is not None:
            parts.append(f"capture={kpis.capture_fps:.2f}")
        if kpis.vision_fps is not None:
            parts.append(f"vision={kpis.vision_fps:.2f}")
        if kpis.decision_fps is not None:
            parts.append(f"decision={kpis.decision_fps:.2f}")
        extra = (" | " + " ".join(parts)) if parts else ""
        print(f"effective_fps: {kpis.effective_fps:.2f}{extra}")

    def fmt_ms(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.1f}ms"

    print(
        "avg capture/vision/decision/e2e: "
        f"{fmt_ms(kpis.avg_capture_ms)} / {fmt_ms(kpis.avg_vision_ms)} / {fmt_ms(kpis.avg_decision_ms)} / {fmt_ms(kpis.avg_end_to_end_ms)}"
    )
    print(
        "p50/p95 capture/vision/decision/e2e: "
        f"{fmt_ms(kpis.p50_capture_ms)}/{fmt_ms(kpis.p95_capture_ms)} "
        f"{fmt_ms(kpis.p50_vision_ms)}/{fmt_ms(kpis.p95_vision_ms)} "
        f"{fmt_ms(kpis.p50_decision_ms)}/{fmt_ms(kpis.p95_decision_ms)} "
        f"{fmt_ms(kpis.p50_end_to_end_ms)}/{fmt_ms(kpis.p95_end_to_end_ms)}"
    )

    def fmt_int(v: int | None) -> str:
        return "n/a" if v is None else str(v)

    def fmt_rate(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.3f}/s"

    print(
        "drops(oldest/new): "
        "frame={}/{} gs={}/{} replay={}/{} jsonl={}/{}".format(
            fmt_int(kpis.drops_frame_oldest),
            fmt_int(kpis.drops_frame_new),
            fmt_int(kpis.drops_gs_oldest),
            fmt_int(kpis.drops_gs_new),
            fmt_int(kpis.drops_replay_oldest),
            fmt_int(kpis.drops_replay_new),
            fmt_int(kpis.drops_jsonl_oldest),
            fmt_int(kpis.drops_jsonl_new),
        )
    )
    print(
        "drop_rates(oldest/new): "
        "frame={}/{} gs={}/{} replay={}/{} jsonl={}/{}".format(
            fmt_rate(kpis.drop_frame_oldest_rate_s),
            fmt_rate(kpis.drop_frame_new_rate_s),
            fmt_rate(kpis.drop_gs_oldest_rate_s),
            fmt_rate(kpis.drop_gs_new_rate_s),
            fmt_rate(kpis.drop_replay_oldest_rate_s),
            fmt_rate(kpis.drop_replay_new_rate_s),
            fmt_rate(kpis.drop_jsonl_oldest_rate_s),
            fmt_rate(kpis.drop_jsonl_new_rate_s),
        )
    )

    if kpis.stale_gamestate_events is not None:
        ratio_s = "n/a" if kpis.stale_gamestate_ratio is None else f"{kpis.stale_gamestate_ratio*100.0:.1f}%"
        rate_s = "n/a" if kpis.stale_gamestate_rate_min is None else f"{kpis.stale_gamestate_rate_min:.2f}/min"
        print(f"stale_gamestate: events={kpis.stale_gamestate_events} ratio={ratio_s} rate={rate_s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
