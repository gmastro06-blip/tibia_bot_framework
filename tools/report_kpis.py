from __future__ import annotations

import argparse
import json
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


@dataclass
class KPIs:
    window_s: float | None
    telemetry_fps: float | None
    n_telemetry: int
    n_health: int
    avg_capture_ms: float | None
    avg_vision_ms: float | None
    avg_decision_ms: float | None
    drops_frame_oldest: int | None
    drops_gs_oldest: int | None
    drops_jsonl_oldest: int | None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_s": self.window_s,
            "telemetry_fps": self.telemetry_fps,
            "n_telemetry": self.n_telemetry,
            "n_health": self.n_health,
            "avg_capture_ms": self.avg_capture_ms,
            "avg_vision_ms": self.avg_vision_ms,
            "avg_decision_ms": self.avg_decision_ms,
            "drops_frame_oldest": self.drops_frame_oldest,
            "drops_gs_oldest": self.drops_gs_oldest,
            "drops_jsonl_oldest": self.drops_jsonl_oldest,
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
    if len(tel) >= 2:
        t0 = _pick_ts(tel[0])
        t1 = _pick_ts(tel[-1])
        if t0 is not None and t1 is not None and t1 > t0:
            window_s = float(t1 - t0)
            telemetry_fps = float(len(tel) / window_s) if window_s > 0 else None

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

    return KPIs(
        window_s=window_s,
        telemetry_fps=telemetry_fps,
        n_telemetry=len(tel),
        n_health=len(health),
        avg_capture_ms=avg_capture_ms,
        avg_vision_ms=avg_vision_ms,
        avg_decision_ms=avg_decision_ms,
        drops_frame_oldest=drops_frame_oldest,
        drops_gs_oldest=drops_gs_oldest,
        drops_jsonl_oldest=drops_jsonl_oldest,
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

    def fmt_ms(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.1f}ms"

    print(f"avg capture/vision/decision: {fmt_ms(kpis.avg_capture_ms)} / {fmt_ms(kpis.avg_vision_ms)} / {fmt_ms(kpis.avg_decision_ms)}")

    def fmt_int(v: int | None) -> str:
        return "n/a" if v is None else str(v)

    print(
        "drops(oldest): frame={} gs={} jsonl={}".format(
            fmt_int(kpis.drops_frame_oldest),
            fmt_int(kpis.drops_gs_oldest),
            fmt_int(kpis.drops_jsonl_oldest),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
