from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _safe_load_json(line: str) -> dict[str, Any] | None:
    line = (line or "").strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize DecisionTrace JSONL (blocked reasons, state counts).")
    ap.add_argument("path", nargs="?", default="logs/decision_trace.jsonl", help="Path to decision_trace.jsonl")
    ap.add_argument("--top", type=int, default=15, help="Show top N reasons")
    args = ap.parse_args()

    p = Path(str(args.path))
    if not p.exists() or not p.is_file():
        print(f"Missing: {p}")
        return 2

    n = 0
    sent = 0
    not_sent = 0
    injection_states: Counter[str] = Counter()
    blocked_reasons: Counter[str] = Counter()
    action_kinds: Counter[str] = Counter()
    capture_backends: Counter[str] = Counter()

    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        ev = _safe_load_json(line)
        if ev is None:
            continue
        n += 1

        inj = ev.get("injection")
        act = ev.get("action")
        cap = ev.get("capture")

        inj_d = inj if isinstance(inj, dict) else {}
        act_d = act if isinstance(act, dict) else {}
        cap_d = cap if isinstance(cap, dict) else {}

        st = str(inj_d.get("state", "") or "")
        br = str(inj_d.get("blocked_reason", "") or "")
        injection_states[st] += 1

        backend = str(cap_d.get("backend", "") or "")
        if backend:
            capture_backends[backend] += 1

        kind = str(act_d.get("kind", "") or "")
        if kind:
            action_kinds[kind] += 1

        if bool(act_d.get("sent_to_driver", False)):
            sent += 1
        else:
            not_sent += 1
            blocked_reasons[br] += 1

    print(f"file={p}")
    print(f"events={n}")
    print(f"sent={sent} not_sent={not_sent}")

    print("\n[injection_state]")
    for k, v in injection_states.most_common(10):
        print(f"{k or '<empty>'}: {v}")

    print("\n[blocked_reason]")
    for k, v in blocked_reasons.most_common(int(args.top)):
        print(f"{k or '<empty>'}: {v}")

    print("\n[action.kind]")
    for k, v in action_kinds.most_common(10):
        print(f"{k}: {v}")

    print("\n[capture.backend]")
    for k, v in capture_backends.most_common(10):
        print(f"{k}: {v}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
