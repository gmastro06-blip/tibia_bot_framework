from __future__ import annotations

import argparse
import sys
from pathlib import Path


def tail_lines(path: str, n: int) -> list[str]:
    """Return the last N lines of a text file (best-effort, efficient)."""

    n = max(0, int(n))
    if n <= 0:
        return []

    p = Path(path)
    if not p.exists() or not p.is_file():
        return []

    # Read backwards in blocks.
    block_size = 64 * 1024
    data = b""
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            pos = f.tell()
            while pos > 0 and data.count(b"\n") <= n:
                take = min(block_size, pos)
                pos -= take
                f.seek(pos)
                chunk = f.read(take)
                data = chunk + data
                if pos == 0:
                    break
    except Exception:
        return []

    # Decode and split.
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return []

    lines = [ln for ln in text.splitlines() if ln != ""]
    return lines[-n:]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Print last N lines of logs/decision_trace.jsonl")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--path", type=str, default="logs/decision_trace.jsonl")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out = tail_lines(args.path, int(args.n))
    for ln in out:
        sys.stdout.write(ln)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
