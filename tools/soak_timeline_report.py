from __future__ import annotations

import argparse
import html
import json
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class IndexedFile:
    ts: float
    path: Path


def _parse_ts_from_stem(stem: str) -> float | None:
    try:
        head = (stem or "").split("_", 1)[0]
        return float(head)
    except Exception:
        return None


def _index_files(dir_path: Path, pattern: str) -> list[IndexedFile]:
    out: list[IndexedFile] = []
    try:
        for p in dir_path.glob(pattern):
            if not p.is_file():
                continue
            ts = _parse_ts_from_stem(p.stem)
            if ts is None:
                continue
            out.append(IndexedFile(ts=float(ts), path=p))
    except Exception:
        return []
    out.sort(key=lambda x: x.ts)
    return out


def _nearest(ts: float, items: list[IndexedFile], *, window_s: float) -> tuple[Path | None, float | None]:
    if not items:
        return None, None
    xs = [x.ts for x in items]
    i = bisect_left(xs, ts)
    cands: list[IndexedFile] = []
    if 0 <= i < len(items):
        cands.append(items[i])
    if i - 1 >= 0:
        cands.append(items[i - 1])

    best: IndexedFile | None = None
    best_dt = 1e18
    for c in cands:
        dt = abs(float(c.ts) - float(ts))
        if dt < best_dt:
            best_dt = dt
            best = c
    if best is None:
        return None, None
    if best_dt > float(window_s):
        return None, None
    return best.path, float(best_dt)


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except Exception:
        return


def _latest_dir(parent: Path) -> Path | None:
    try:
        dirs = [p for p in parent.iterdir() if p.is_dir()]
        if not dirs:
            return None
        dirs.sort(key=lambda p: p.name)
        return dirs[-1]
    except Exception:
        return None


def _pick_default_paths(repo_root: Path) -> tuple[Path | None, Path | None, Path | None]:
    logs = repo_root / "logs"

    replay_dir: Path | None = None
    overlay_dir: Path | None = None
    jsonl_path: Path | None = None

    replay_soak_parent = logs / "replay_soak"
    if replay_soak_parent.exists():
        replay_dir = _latest_dir(replay_soak_parent)

    overlay_soak_parent = logs / "debug_overlay_soak"
    if overlay_soak_parent.exists():
        overlay_dir = _latest_dir(overlay_soak_parent)

    try:
        jsonl_cands = sorted(logs.glob("telemetry_soak_*.jsonl"), key=lambda p: p.name)
        if jsonl_cands:
            jsonl_path = jsonl_cands[-1]
    except Exception:
        jsonl_path = None

    if replay_dir is None:
        replay_dir = logs / "replay"
    if overlay_dir is None:
        overlay_dir = logs / "debug_overlay"
    if jsonl_path is None:
        jsonl_path = logs / "telemetry.jsonl"

    return replay_dir, overlay_dir, jsonl_path


def _rel_link(from_path: Path, to_path: Path) -> str:
    try:
        return to_path.relative_to(from_path.parent).as_posix()
    except Exception:
        try:
            return to_path.as_posix()
        except Exception:
            return str(to_path)


def _h(v: Any) -> str:
    try:
        return html.escape(str(v))
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate an HTML timeline from soak artifacts (JSONL + replay + overlay).")
    ap.add_argument("--jsonl", default="", help="Path to telemetry JSONL (defaults to latest soak JSONL).")
    ap.add_argument("--replay-dir", default="", help="Replay dir (defaults to latest logs/replay_soak/*).")
    ap.add_argument("--overlay-dir", default="", help="Overlay dir (defaults to latest logs/debug_overlay_soak/*).")
    ap.add_argument("--out", default="logs/soak_timeline.html", help="Output HTML path.")
    ap.add_argument("--limit", type=int, default=2000, help="Max rows in the table.")
    ap.add_argument(
        "--match-window-s",
        type=float,
        default=1.5,
        help="Max allowed seconds to link a row to nearest replay/overlay file.",
    )
    ap.add_argument(
        "--include",
        default="telemetry,event.*",
        help="Comma-separated kinds to include (supports '*' wildcard). Default: telemetry,event.*",
    )
    ap.add_argument(
        "--only-committed",
        action="store_true",
        help="Keep only rows where action_committed is true.",
    )
    ap.add_argument(
        "--only-action-events",
        action="store_true",
        help="Keep only action-related rows (kind==event.action_request or action_request non-empty).",
    )
    ap.add_argument(
        "--grep-action",
        default="",
        help="Case-insensitive substring filter applied to action_request.",
    )
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    default_replay, default_overlay, default_jsonl = _pick_default_paths(repo_root)

    jsonl_path = (
        Path(args.jsonl).resolve() if str(args.jsonl).strip() else (default_jsonl or Path("logs/telemetry.jsonl"))
    )
    replay_dir = (
        Path(args.replay_dir).resolve() if str(args.replay_dir).strip() else (default_replay or Path("logs/replay"))
    )
    overlay_dir = (
        Path(args.overlay_dir).resolve()
        if str(args.overlay_dir).strip()
        else (default_overlay or Path("logs/debug_overlay"))
    )

    out_path = Path(args.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    if not jsonl_path.exists():
        print(f"ERROR: jsonl not found: {jsonl_path}")
        return 2

    replay_index = _index_files(replay_dir, "*.json") if replay_dir.exists() else []
    replay_index = [x for x in replay_index if x.path.name != "log.json"]
    overlay_index = _index_files(overlay_dir, "*.png") if overlay_dir.exists() else []

    def _split_csv(s: str) -> list[str]:
        return [x.strip() for x in (s or "").split(",") if x.strip()]

    include_kinds = _split_csv(str(args.include)) or ["telemetry", "event.*"]

    def _kind_allowed(kind: str) -> bool:
        if not kind:
            return False
        for pat in include_kinds:
            if pat == "*":
                return True
            if pat.endswith("*") and kind.startswith(pat[:-1]):
                return True
            if kind == pat:
                return True
        return False

    grep_action = str(args.grep_action or "").strip().lower()

    rows: list[dict[str, Any]] = []
    for ev in _iter_jsonl(jsonl_path):
        kind = str(ev.get("kind", "") or "")
        if not _kind_allowed(kind):
            continue

        ts = ev.get("ts", None)
        try:
            ts_f = float(ts)
        except Exception:
            continue
        ev["ts"] = ts_f

        try:
            ar = str(ev.get("action_request", "") or "")
        except Exception:
            ar = ""
        try:
            ac = bool(ev.get("action_committed", False))
        except Exception:
            ac = False

        if args.only_committed and not ac:
            continue
        if args.only_action_events and not (kind == "event.action_request" or bool(ar)):
            continue
        if grep_action and (grep_action not in ar.lower()):
            continue

        rows.append(ev)

    rows.sort(key=lambda e: float(e.get("ts", 0.0) or 0.0))
    if args.limit and len(rows) > int(args.limit):
        rows = rows[-int(args.limit) :]

    committed_n = 0
    action_event_n = 0
    has_inputs_n = 0
    action_req_counter: Counter[str] = Counter()
    committed_action_req_counter: Counter[str] = Counter()
    for ev in rows:
        try:
            if bool(ev.get("action_committed", False)):
                committed_n += 1
        except Exception:
            pass
        try:
            if str(ev.get("kind", "") or "") == "event.action_request":
                action_event_n += 1
        except Exception:
            pass
        try:
            ar = str(ev.get("action_request", "") or "").strip()
            if ar:
                action_req_counter[ar] += 1
                if bool(ev.get("action_committed", False)):
                    committed_action_req_counter[ar] += 1
        except Exception:
            pass
        try:
            if str(ev.get("input_plan", "") or "").strip():
                has_inputs_n += 1
        except Exception:
            pass

    def _top(counter: Counter[str], n: int = 15) -> list[tuple[str, int]]:
        try:
            return list(counter.most_common(n))
        except Exception:
            return []

    header = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Soak timeline</title>"
        "<style>"
        "body{font-family:Segoe UI,Arial,sans-serif;margin:20px;}"
        "input[type=text]{padding:8px 10px;border:1px solid #ccc;border-radius:6px;width:min(900px,95%);}"
        "table{border-collapse:collapse;width:100%;font-size:12px;}"
        "th,td{border:1px solid #ddd;padding:6px;vertical-align:top;}"
        "th{background:#f5f5f5;position:sticky;top:0;}"
        ".muted{color:#666;}"
        ".mono{font-family:Consolas,Menlo,monospace;}"
        ".danger{background:#ffdddd;}"
        ".committed{background:#eaffea;}"
        ".has_inputs:not(.danger):not(.committed){background:#e8f4ff;}"
        ".chip{display:inline-block;margin:2px 6px 2px 0;padding:2px 8px;border-radius:999px;background:#f2f2f2;}"
        "details{margin:10px 0;}"
        "</style></head><body>"
    )

    meta = f"""
<h2>Soak timeline</h2>
<div class='muted mono'>jsonl={_h(jsonl_path)}</div>
<div class='muted mono'>replay_dir={_h(replay_dir)} (files={len(replay_index)})</div>
<div class='muted mono'>overlay_dir={_h(overlay_dir)} (files={len(overlay_index)})</div>
<div class='muted mono'>rows={len(rows)} window_s={float(args.match_window_s):.2f}</div>
<div class='muted mono'>include={_h(args.include)} only_committed={int(bool(args.only_committed))} only_action_events={int(bool(args.only_action_events))} grep_action={_h(args.grep_action)}</div>

<div style='margin:10px 0'>
  <span class='chip mono'>committed={committed_n}</span>
  <span class='chip mono'>event.action_request={action_event_n}</span>
  <span class='chip mono'>unique_actions={len(action_req_counter)}</span>
    <span class='chip mono'>with_input_plan={has_inputs_n}</span>
</div>

<div style='margin:10px 0'>
  <div class='muted'>In-page filter (client-side):</div>
  <input id='filterBox' type='text' placeholder='type to filter rows (matches any cell text)…' />
</div>

<details>
  <summary class='mono'>Top action_request (frequency)</summary>
  <div class='mono'>
    {'<br/>'.join(_h(f'{k}  ×{v}') for k, v in _top(action_req_counter)) or '<span class="muted">(none)</span>'}
  </div>
</details>

<details>
  <summary class='mono'>Top committed action_request</summary>
  <div class='mono'>
    {'<br/>'.join(_h(f'{k}  ×{v}') for k, v in _top(committed_action_req_counter)) or '<span class="muted">(none)</span>'}
  </div>
</details>
"""

    cols = [
        "ts",
        "kind",
        "action_request",
        "action_committed",
        "action_ts",
        "input_plan",
        "hp_pct",
        "mp_pct",
        "target",
        "recommendation",
        "cavebot_next",
        "note",
        "stuck_reason",
        "links",
    ]

    out_lines: list[str] = [header, meta, "<table><thead><tr>"]
    for c in cols:
        out_lines.append(f"<th class='mono'>{_h(c)}</th>")
    out_lines.append("</tr></thead><tbody>")

    for ev in rows:
        ts = float(ev.get("ts", 0.0) or 0.0)
        replay_path, replay_dt = _nearest(ts, replay_index, window_s=float(args.match_window_s))
        overlay_path, overlay_dt = _nearest(ts, overlay_index, window_s=float(args.match_window_s))

        links: list[str] = []
        if replay_path is not None and replay_dt is not None:
            href = _rel_link(out_path, replay_path)
            links.append(f"<a class='mono' href='{_h(href)}'>replay</a> <span class='muted'>Δ{replay_dt:.2f}s</span>")
        if overlay_path is not None and overlay_dt is not None:
            href = _rel_link(out_path, overlay_path)
            links.append(f"<a class='mono' href='{_h(href)}'>overlay</a> <span class='muted'>Δ{overlay_dt:.2f}s</span>")
        links_html = "<br/>".join(links)

        danger = False
        try:
            danger = bool(ev.get("low_hp")) or bool(ev.get("paralyzed"))
        except Exception:
            danger = False

        committed = False
        try:
            committed = bool(ev.get("action_committed", False))
        except Exception:
            committed = False

        tr_classes: list[str] = []
        if danger:
            tr_classes.append("danger")
        if committed:
            tr_classes.append("committed")
            try:
                if str(ev.get("input_plan", "") or "").strip():
                    tr_classes.append("has_inputs")
            except Exception:
                pass
        tr_cls = " ".join(tr_classes)

        def cell(key: str) -> str:
            v = ev.get(key, "")
            if key in {"action_committed"}:
                return "1" if bool(v) else "0"
            if key in {"ts", "action_ts"}:
                try:
                    return f"{float(v):.6f}" if v not in (None, "") else ""
                except Exception:
                    return _h(v)
            return _h(v)

        out_lines.append(f"<tr class='{tr_cls}'>")
        for c in cols:
            if c == "links":
                out_lines.append(f"<td>{links_html}</td>")
            else:
                out_lines.append(f"<td class='mono'>{cell(c)}</td>")
        out_lines.append("</tr>")

    out_lines.append("</tbody></table>")

    out_lines.append(
        """
<script>
(() => {
  const box = document.getElementById('filterBox');
  if (!box) return;
  const tbody = document.querySelector('tbody');
  if (!tbody) return;
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const norm = (s) => (s || '').toString().toLowerCase();
  const apply = () => {
    const q = norm(box.value).trim();
    for (const tr of rows) {
      const txt = norm(tr.innerText);
      tr.style.display = (!q || txt.includes(q)) ? '' : 'none';
    }
  };
  box.addEventListener('input', apply);
  apply();
})();
</script>
""".strip()
    )
    out_lines.append("</body></html>")

    try:
        out_path.write_text("\n".join(out_lines), encoding="utf-8")
    except Exception as e:
        print(f"ERROR: could not write {out_path}: {e}")
        return 3

    print(f"OK: wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
