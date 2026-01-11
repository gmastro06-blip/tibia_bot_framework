from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from tools.coords_to_route import _parse_coords_line


def _get_clipboard_text() -> str:
    """Read Windows clipboard text via PowerShell (no extra deps)."""

    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard | Out-String"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if res.returncode != 0:
            return ""
        return (res.stdout or "").strip()
    except Exception:
        return ""


def _safe_write_json(path: Path, data: object) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Writes COORDS_FILE-style JSON from clipboard text (safe: no inputs). "
            "Useful with COORDS_PROVIDER=file when you don't have on-screen coords."
        )
    )
    p.add_argument(
        "--out",
        default=str(Path("logs") / "coords.json"),
        help="Output JSON path (default: logs/coords.json)",
    )
    p.add_argument(
        "--poll-s",
        type=float,
        default=float(os.getenv("COORDS_CLIPBOARD_POLL_S", "0.15") or 0.15),
        help="Clipboard poll interval (seconds)",
    )
    p.add_argument(
        "--seconds",
        type=float,
        default=0.0,
        help="Run duration in seconds (0 = until Ctrl+C)",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Write once from current clipboard and exit.",
    )
    p.add_argument(
        "--print",
        action="store_true",
        help="Print whenever coords are written.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    out_path = Path(str(args.out)).resolve()

    print(f"🧾 Writing coords file: {out_path}")
    print("📋 Copy coords to clipboard, e.g.:")
    print("   - 'X: 32561 Y: 32496 Z: 7'")
    print("   - '32561 32496 7'")
    print("   - '32561,32496,7'")

    if args.once:
        text = _get_clipboard_text()
        parsed = _parse_coords_line(text)
        if parsed is None:
            print("❌ No coords parsed from clipboard.")
            return 2
        x, y, z = parsed
        payload = {"x": int(x), "y": int(y), "z": (int(z) if z is not None else None), "ts": time.time()}
        _safe_write_json(out_path, payload)
        if args.print:
            print(f"✅ Wrote coords: {payload}")
        return 0

    end_ts = (time.time() + float(args.seconds)) if float(args.seconds) > 0 else None

    last_clip = ""
    last_coords: tuple[int, int, int | None] | None = None

    try:
        while True:
            if end_ts is not None and time.time() >= float(end_ts):
                break

            cur = _get_clipboard_text()
            if cur and cur != last_clip:
                last_clip = cur
                parsed = _parse_coords_line(cur)
                if parsed is not None and parsed != last_coords:
                    last_coords = parsed
                    x, y, z = parsed
                    payload = {
                        "x": int(x),
                        "y": int(y),
                        "z": (int(z) if z is not None else None),
                        "ts": time.time(),
                    }
                    _safe_write_json(out_path, payload)
                    if args.print:
                        if payload["z"] is None:
                            print(f"✅ {payload['x']},{payload['y']}  (wrote)")
                        else:
                            print(f"✅ {payload['x']},{payload['y']},{payload['z']}  (wrote)")

            time.sleep(max(0.05, float(args.poll_s)))

    except KeyboardInterrupt:
        print("\n⏹️  stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
