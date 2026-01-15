from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


def _iter_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def _fmt_ts(ts: Any) -> str:
    try:
        t = float(ts)
        return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"


def _pick_action_requests(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = ev.get("action_requests")
    if isinstance(raw, list) and raw:
        out: List[Dict[str, Any]] = []
        for x in raw:
            if isinstance(x, dict):
                out.append(dict(x))
        if out:
            return out

    tel = ev.get("telemetry")
    if isinstance(tel, dict):
        raw2 = tel.get("action_requests")
        if isinstance(raw2, list) and raw2:
            out2: List[Dict[str, Any]] = []
            for x in raw2:
                if isinstance(x, dict):
                    out2.append(dict(x))
            if out2:
                return out2

    return []


def _fmt_action_requests(reqs: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for r in reqs:
        try:
            kind = str(r.get("kind", "") or "").strip()
            value = str(r.get("value", "") or "").strip()
            committed = bool(r.get("committed", False))
            note = str(r.get("note", "") or "").strip().lower()
            star = "*" if committed or note == "committed" else ""
            if kind or value:
                parts.append(f"{kind}:{value}{star}" if kind else f"{value}{star}")
        except Exception:
            continue
    return ";".join(parts)


def _pick_action_str(ev: Dict[str, Any]) -> str:
    # Prefer structured list (no parsing).
    reqs = _pick_action_requests(ev)
    if reqs:
        s = _fmt_action_requests(reqs)
        if s:
            return s

    # Prefer explicit event payload.
    ar = ev.get("action_request")
    if isinstance(ar, str) and ar.strip():
        return ar.strip()

    # Fallback: some events might nest telemetry.
    tel = ev.get("telemetry")
    if isinstance(tel, dict):
        ar2 = tel.get("action_request")
        if isinstance(ar2, str) and ar2.strip():
            return ar2.strip()

    return ""


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Print mock action requests from telemetry JSONL (assistant mode)."
    )
    p.add_argument(
        "--file",
        default=os.getenv("TELEMETRY_JSONL", "logs/telemetry.jsonl"),
        help="Path to JSONL file (default: logs/telemetry.jsonl or TELEMETRY_JSONL env var)",
    )
    p.add_argument(
        "--last",
        type=int,
        default=50,
        help="Show only the last N matching entries (default: 50)",
    )
    p.add_argument(
        "--kind",
        default="event.action_request",
        help="Filter by event kind (default: event.action_request). Use 'any' to match all.",
    )
    p.add_argument(
        "--include-telemetry",
        action="store_true",
        help="Also include kind='telemetry' lines when they have non-empty action_requests/action_request.",
    )

    args = p.parse_args(argv)

    path = str(args.file)
    if not os.path.exists(path):
        print(f"File not found: {path}", file=sys.stderr)
        return 2

    want_kind = str(args.kind).strip()
    matches: List[Dict[str, Any]] = []

    for ev in _iter_jsonl(path):
        kind = ev.get("kind")
        if want_kind.lower() != "any":
            if kind != want_kind:
                # optional: include telemetry lines with action_request
                if args.include_telemetry and kind == "telemetry":
                    if not _pick_action_str(ev):
                        continue
                else:
                    continue

        act = _pick_action_str(ev)
        if not act:
            # avoid noise
            continue
        matches.append(ev)

    if args.last > 0:
        matches = matches[-int(args.last) :]

    for ev in matches:
        ts = _fmt_ts(ev.get("ts"))
        kind = ev.get("kind", "")
        act = _pick_action_str(ev)
        committed = ev.get("action_committed")
        if committed is None and isinstance(ev.get("telemetry"), dict):
            committed = ev["telemetry"].get("action_committed")
        committed_str = "committed" if bool(committed) else "preview"

        extra = []
        reco = ev.get("recommendation")
        if isinstance(reco, str) and reco.strip():
            extra.append(f"reco={reco.strip()}")
        cnext = ev.get("cavebot_next")
        if isinstance(cnext, str) and cnext.strip():
            extra.append(f"next={cnext.strip()}")

        tail = (" | " + ", ".join(extra)) if extra else ""
        print(f"{ts} | {kind} | {committed_str} | {act}{tail}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
