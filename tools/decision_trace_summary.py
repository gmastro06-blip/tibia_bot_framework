from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


def _safe_load_json(line: str) -> dict[str, Any] | None:
    line = (line or "").strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _iter_lines(p: Path) -> Iterable[str]:
    # Stream (avoid loading huge JSONL into memory).
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                yield ln
    except Exception:
        return


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize DecisionTrace JSONL (blocked reasons, state counts).")
    ap.add_argument("path", nargs="?", default="logs/decision_trace.jsonl", help="Path to decision_trace.jsonl")
    ap.add_argument("--top", type=int, default=15, help="Show top N reasons")
    ap.add_argument(
        "--stats",
        action="store_true",
        help="Print JSONL parsing stats (malformed/non-dict lines)",
    )
    ap.add_argument("--only-sent", action="store_true", help="Only count events where sent_to_driver is true")
    ap.add_argument("--only-not-sent", action="store_true", help="Only count events where sent_to_driver is false")
    ap.add_argument("--state", action="append", default=None, help="Filter by injection.state (repeatable)")
    ap.add_argument(
        "--blocked-reason",
        action="append",
        default=None,
        help="Filter by injection.blocked_reason (repeatable; only applies to not-sent)",
    )
    ap.add_argument("--backend", action="append", default=None, help="Filter by capture.backend (repeatable)")
    ap.add_argument(
        "--examples",
        type=int,
        default=0,
        help="Print up to N example lines for the top blocked reasons (0 disables)",
    )
    ap.add_argument(
        "--since-s",
        type=float,
        default=0.0,
        help="Only include events from the last N seconds (0 disables)",
    )
    ap.add_argument(
        "--since-ts",
        type=float,
        default=0.0,
        help="Only include events with ts >= this unix timestamp (0 disables)",
    )
    args = ap.parse_args()

    p = Path(str(args.path))
    if not p.exists() or not p.is_file():
        print(f"Missing: {p}")
        return 2

    # Optional time window filter. To keep streaming behavior, we may do a
    # cheap pre-scan to find the most recent ts.
    since_ts: float | None = None

    # Parse robustness stats (useful when a writer crashes and leaves partial lines).
    lines_total = 0
    lines_bad_json = 0
    lines_non_dict = 0
    try:
        if float(args.since_ts or 0.0) > 0.0:
            since_ts = float(args.since_ts)
    except Exception:
        since_ts = None

    if since_ts is None:
        try:
            win_s = float(args.since_s or 0.0)
        except Exception:
            win_s = 0.0
        if win_s > 0.0:
            last_ts_scan: float | None = None
            for line in _iter_lines(p):
                lines_total += 1
                ev = _safe_load_json(line)
                if ev is None:
                    # Best-effort: distinguish "bad json" vs "non-dict".
                    s = (line or "").strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                        if not isinstance(obj, dict):
                            lines_non_dict += 1
                    except Exception:
                        lines_bad_json += 1
                    continue
                raw_ts = ev.get("ts")
                try:
                    if isinstance(raw_ts, (int, float)):
                        ts0 = float(raw_ts)
                    elif isinstance(raw_ts, str) and raw_ts.strip():
                        ts0 = float(raw_ts.strip())
                    else:
                        ts0 = None
                except Exception:
                    ts0 = None
                if ts0 is None:
                    continue
                last_ts_scan = ts0 if last_ts_scan is None else max(last_ts_scan, ts0)
            if last_ts_scan is not None:
                since_ts = float(last_ts_scan) - float(win_s)

    n_total = 0
    n = 0
    sent = 0
    not_sent = 0
    injection_states: Counter[str] = Counter()
    blocked_reasons: Counter[str] = Counter()
    action_kinds: Counter[str] = Counter()
    action_kinds_sent: Counter[str] = Counter()
    action_kinds_not_sent: Counter[str] = Counter()
    capture_backends: Counter[str] = Counter()
    capture_titles: Counter[str] = Counter()
    capture_states: Counter[str] = Counter()
    capture_monitors: Counter[str] = Counter()

    # Helpful cross-tabs for debugging.
    state_block_pairs: Counter[tuple[str, str]] = Counter()
    kind_block_pairs: Counter[tuple[str, str]] = Counter()

    # Optional example lines per blocked_reason (for quick inspection).
    examples: dict[str, list[str]] = {}

    def _want(val: str, allow: list[str] | None) -> bool:
        if not allow:
            return True
        return val in allow

    state_allow = [str(s) for s in (args.state or [])] if args.state else None
    backend_allow = [str(b) for b in (args.backend or [])] if args.backend else None
    br_allow = [str(r) for r in (args.blocked_reason or [])] if args.blocked_reason else None

    first_ts: float | None = None
    last_ts: float | None = None

    for line in _iter_lines(p):
        lines_total += 1
        ev = _safe_load_json(line)
        if ev is None:
            s = (line or "").strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
                if not isinstance(obj, dict):
                    lines_non_dict += 1
            except Exception:
                lines_bad_json += 1
            continue
        n_total += 1

        inj = ev.get("injection")
        act = ev.get("action")
        cap = ev.get("capture")

        inj_d = inj if isinstance(inj, dict) else {}
        act_d = act if isinstance(act, dict) else {}
        cap_d = cap if isinstance(cap, dict) else {}

        st = str(inj_d.get("state", "") or "")
        br = str(inj_d.get("blocked_reason", "") or "")

        if not _want(st, state_allow):
            continue

        # Parse ts (used for optional time filtering + span).
        raw_ts = ev.get("ts")
        ts: float | None
        try:
            if isinstance(raw_ts, (int, float)):
                ts = float(raw_ts)
            elif isinstance(raw_ts, str) and raw_ts.strip():
                ts = float(raw_ts.strip())
            else:
                ts = None
        except Exception:
            ts = None
        # Apply time window filter before counting anything else.
        if since_ts is not None and ts is not None and float(ts) < float(since_ts):
            continue

        # Track span for included events.
        if ts is not None:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)

        backend = str(cap_d.get("backend", "") or "")
        if not _want(backend, backend_allow):
            continue
        capture_backends[backend or "<empty>"] += 1

        cstate = str(cap_d.get("state", "") or "")
        capture_states[cstate or "<empty>"] += 1

        mon = cap_d.get("monitor_index", None)
        mon_s = "<empty>"
        try:
            if mon is not None:
                mon_s = str(int(mon))
        except Exception:
            mon_s = "<empty>"
        capture_monitors[mon_s] += 1

        title = str(cap_d.get("target_title", "") or "")
        capture_titles[title or "<empty>"] += 1

        kind = str(act_d.get("kind", "") or "")
        if kind:
            action_kinds[kind] += 1

        sent_to_driver = bool(act_d.get("sent_to_driver", False))

        if args.only_sent and not sent_to_driver:
            continue
        if args.only_not_sent and sent_to_driver:
            continue

        # This event is included by the active filters.
        n += 1

        injection_states[st] += 1

        if sent_to_driver:
            sent += 1
            if kind:
                action_kinds_sent[kind] += 1
        else:
            not_sent += 1
            if kind:
                action_kinds_not_sent[kind] += 1

            if (br_allow is not None) and (br not in br_allow):
                continue

            blocked_reasons[br] += 1

            # Cross-tabs: why we didn't send.
            state_block_pairs[(st or "", br or "")] += 1
            if kind:
                kind_block_pairs[(kind, br or "")] += 1

            if int(args.examples or 0) > 0:
                # Store a compact one-line sample (keep raw JSON for copy/paste).
                k = br
                if k not in examples:
                    examples[k] = []
                if len(examples[k]) < int(args.examples):
                    try:
                        examples[k].append(line.strip())
                    except Exception:
                        pass

    print(f"file={p}")
    if since_ts is not None:
        print(f"since_ts={since_ts:.3f}")
    if bool(getattr(args, "stats", False)):
        print(f"lines_total={lines_total} bad_json={lines_bad_json} non_dict={lines_non_dict}")
    print(f"events={n}")
    if n:
        sent_rate = (float(sent) / float(n)) * 100.0
    else:
        sent_rate = 0.0
    print(f"sent={sent} not_sent={not_sent} sent_rate={sent_rate:.1f}%")

    if first_ts is not None and last_ts is not None and last_ts >= first_ts:
        span_s = float(last_ts - first_ts)
        print(f"span_s={span_s:.1f}")

    print("\n[injection_state]")
    for k, v in injection_states.most_common(10):
        pct = (float(v) / float(n) * 100.0) if n else 0.0
        print(f"{k or '<empty>'}: {v} ({pct:.1f}%)")

    print("\n[blocked_reason]")
    for k, v in blocked_reasons.most_common(int(args.top)):
        base = float(not_sent) if not_sent else float(n or 1)
        pct = (float(v) / base) * 100.0
        print(f"{k or '<empty>'}: {v} ({pct:.1f}%)")
        if int(args.examples or 0) > 0:
            for ex in examples.get(k, [])[: int(args.examples)]:
                print(f"  ex: {ex}")

    print("\n[action.kind]")
    for k, v in action_kinds.most_common(10):
        pct = (float(v) / float(n) * 100.0) if n else 0.0
        print(f"{k}: {v} ({pct:.1f}%)")

    print("\n[sent.action.kind]")
    for k, v in action_kinds_sent.most_common(10):
        base = float(sent) if sent else 1.0
        pct = (float(v) / base) * 100.0
        print(f"{k}: {v} ({pct:.1f}%)")

    print("\n[not_sent.action.kind]")
    for k, v in action_kinds_not_sent.most_common(10):
        base = float(not_sent) if not_sent else 1.0
        pct = (float(v) / base) * 100.0
        print(f"{k}: {v} ({pct:.1f}%)")

    print("\n[not_sent.state+blocked_reason]")
    for (st0, br0), v in state_block_pairs.most_common(int(min(10, args.top))):
        base = float(not_sent) if not_sent else 1.0
        pct = (float(v) / base) * 100.0
        print(f"{st0 or '<empty>'} | {br0 or '<empty>'}: {v} ({pct:.1f}%)")

    print("\n[not_sent.kind+blocked_reason]")
    for (k0, br0), v in kind_block_pairs.most_common(int(min(10, args.top))):
        base = float(not_sent) if not_sent else 1.0
        pct = (float(v) / base) * 100.0
        print(f"{k0} | {br0 or '<empty>'}: {v} ({pct:.1f}%)")

    print("\n[capture.backend]")
    for k, v in capture_backends.most_common(10):
        pct = (float(v) / float(n) * 100.0) if n else 0.0
        print(f"{k}: {v} ({pct:.1f}%)")

    print("\n[capture.state]")
    for k, v in capture_states.most_common(10):
        pct = (float(v) / float(n) * 100.0) if n else 0.0
        print(f"{k}: {v} ({pct:.1f}%)")

    print("\n[capture.monitor_index]")
    for k, v in capture_monitors.most_common(10):
        pct = (float(v) / float(n) * 100.0) if n else 0.0
        print(f"{k}: {v} ({pct:.1f}%)")

    print("\n[capture.target_title]")
    for k, v in capture_titles.most_common(5):
        pct = (float(v) / float(n) * 100.0) if n else 0.0
        s = k
        if len(s) > 120:
            s = s[:117] + "..."
        print(f"{s}: {v} ({pct:.1f}%)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
