from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class WPLine:
    kind: str  # label|action|node|stand|rope|ladder|...
    raw: str
    label: Optional[str] = None
    action: Optional[str] = None
    pos: Optional[Tuple[int, int, int]] = None


_POS_RE = re.compile(r"\((\s*-?\d+\s*),(\s*-?\d+\s*),(\s*-?\d+\s*)\)")


def parse_waypoints_in(path: Path) -> List[WPLine]:
    out: List[WPLine] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//"):
            continue

        # Normalize whitespace
        line = re.sub(r"\s+", " ", line)

        if line.lower().startswith("label "):
            name = line.split(" ", 1)[1].strip()
            out.append(WPLine(kind="label", raw=raw, label=name))
            continue

        if line.lower().startswith("action "):
            name = line.split(" ", 1)[1].strip()
            out.append(WPLine(kind="action", raw=raw, action=name))
            continue

        # Generic command with a position: node (x,y,z), stand (x,y,z), rope (x,y,z), ladder (x,y,z)...
        parts = line.split(" ", 1)
        cmd = parts[0].strip().lower()
        m = _POS_RE.search(line)
        if m:
            x = int(m.group(1))
            y = int(m.group(2))
            z = int(m.group(3))
            out.append(WPLine(kind=cmd, raw=raw, pos=(x, y, z)))
            continue

        # Fallback unknown
        out.append(WPLine(kind="unknown", raw=raw))

    return out


def _extract_global_actions(global_actions_py: Path) -> List[str]:
    text = global_actions_py.read_text(encoding="utf-8", errors="replace")
    names = set(re.findall(r"\baction\s*==\s*\"([^\"]+)\"", text))
    # keep deterministic order
    return sorted(names)


def _iter_setup_jsons(folder: Path) -> Iterable[Path]:
    for p in folder.glob("setup*.json"):
        if p.is_file():
            yield p


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _extract_setup_actions(setup: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {
        "label_actions": [],
        "persistent_actions": [],
        "target_monsters": [],
    }

    la = setup.get("label_actions")
    if isinstance(la, list):
        for item in la:
            if isinstance(item, dict):
                out["label_actions"].append(item)

    pa = setup.get("persistent_actions")
    if isinstance(pa, list):
        for item in pa:
            if isinstance(item, dict):
                out["persistent_actions"].append(item)

    tm = setup.get("target_monsters")
    if isinstance(tm, list):
        for item in tm:
            if isinstance(item, dict):
                out["target_monsters"].append(item)

    return out


def _pos_points(lines: Sequence[WPLine]) -> List[Tuple[int, int, int, int]]:
    pts: List[Tuple[int, int, int, int]] = []
    for i, w in enumerate(lines):
        if w.pos is None:
            continue
        x, y, z = w.pos
        pts.append((i, x, y, z))
    return pts


def analyze_route(lines: Sequence[WPLine]) -> Dict[str, Any]:
    pts = _pos_points(lines)

    # Basic stats
    total_manhattan = 0
    total_euclid = 0.0
    max_manhattan = 0
    max_jump: Optional[Dict[str, Any]] = None

    jump_threshold = int(os.getenv("CLOUDBOT_JUMP_THRESHOLD", "50"))
    odd_jumps: List[Dict[str, Any]] = []

    prev = None
    for (idx, x, y, z) in pts:
        if prev is None:
            prev = (idx, x, y, z)
            continue
        pidx, px, py, pz = prev
        dx = x - px
        dy = y - py
        dz = z - pz
        man = abs(dx) + abs(dy)
        euc = (dx * dx + dy * dy) ** 0.5
        total_manhattan += man
        total_euclid += euc

        if man > max_manhattan:
            max_manhattan = man
            max_jump = {
                "from": {"line_index": pidx, "x": px, "y": py, "z": pz},
                "to": {"line_index": idx, "x": x, "y": y, "z": z},
                "dx": dx,
                "dy": dy,
                "dz": dz,
                "manhattan": man,
                "euclid": euc,
            }

        if man >= jump_threshold:
            odd_jumps.append(
                {
                    "from": {"line_index": pidx, "x": px, "y": py, "z": pz},
                    "to": {"line_index": idx, "x": x, "y": y, "z": z},
                    "dx": dx,
                    "dy": dy,
                    "dz": dz,
                    "manhattan": man,
                    "euclid": euc,
                }
            )

        prev = (idx, x, y, z)

    # Loop-ish detection: repeated coordinates
    seen: Dict[Tuple[int, int, int], int] = {}
    repeats: List[Dict[str, Any]] = []
    for (idx, x, y, z) in pts:
        key = (x, y, z)
        if key in seen:
            repeats.append({"pos": {"x": x, "y": y, "z": z}, "first_line": seen[key], "again_line": idx})
        else:
            seen[key] = idx

    # Labels and actions
    labels = [w.label for w in lines if w.kind == "label" and w.label]
    actions = [w.action for w in lines if w.kind == "action" and w.action]

    return {
        "counts": {
            "lines": len(lines),
            "pos_points": len(pts),
            "labels": len(labels),
            "actions": len(actions),
            "unique_actions": len(set(actions)),
        },
        "distance": {
            "total_manhattan": total_manhattan,
            "total_euclid": total_euclid,
            "max_manhattan": max_manhattan,
            "max_jump": max_jump,
            "jump_threshold": jump_threshold,
            "odd_jumps": odd_jumps,
        },
        "loops": {
            "repeats": repeats,
            "repeat_positions": len({(r["pos"]["x"], r["pos"]["y"], r["pos"]["z"]) for r in repeats}),
        },
        "labels": labels,
        "actions": sorted({a for a in actions if a}),
    }


def save_route_plot(lines: Sequence[WPLine], out_png: Path, title: str) -> None:
    # Lazy import so this tool can still run in minimal envs.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = _pos_points(lines)
    if not pts:
        # Create placeholder
        out_png.parent.mkdir(parents=True, exist_ok=True)
        out_png.write_bytes(b"")
        return

    xs = [x for (_i, x, _y, _z) in pts]
    ys = [y for (_i, _x, y, _z) in pts]
    zs = [z for (_i, _x, _y, z) in pts]

    fig = plt.figure(figsize=(10, 10))
    ax = fig.add_subplot(1, 1, 1)
    sc = ax.scatter(xs, ys, c=zs, s=8, cmap="viridis")
    ax.plot(xs, ys, linewidth=0.8, alpha=0.6)

    # Tibia coords: y increases south; invert to make north up.
    ax.invert_yaxis()

    ax.set_title(title)
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    cbar = fig.colorbar(sc, ax=ax)
    cbar.set_label("z")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def analyze_scripts(scripts_root: Path, out_dir: Path) -> Dict[str, Any]:
    global_actions_path = scripts_root / "global_actions.py"
    supported_actions = _extract_global_actions(global_actions_path) if global_actions_path.exists() else []
    supported_set = set(supported_actions)

    summary: Dict[str, Any] = {
        "scripts_root": str(scripts_root),
        "supported_waypoint_actions": supported_actions,
        "scripts": {},
        "inventory": {
            "waypoints_actions_used": {},
            "setup_label_actions_used": {},
            "setup_persistent_actions_used": {},
            "missing_waypoint_actions": [],
            "missing_setup_actions": [],
        },
    }

    used_waypoint_actions: Dict[str, int] = {}
    used_setup_actions: Dict[str, int] = {}
    used_persistent_actions: Dict[str, int] = {}

    # Each folder with a waypoints.in
    for wp_path in scripts_root.rglob("waypoints.in"):
        folder = wp_path.parent
        script_name = str(folder.relative_to(scripts_root)).replace("\\", "/")

        lines = parse_waypoints_in(wp_path)
        route_info = analyze_route(lines)

        # setup json(s)
        setups: List[Dict[str, Any]] = []
        setup_actions: Dict[str, Any] = {"label_actions": [], "persistent_actions": [], "target_monsters": []}
        for setup_path in _iter_setup_jsons(folder):
            try:
                setup_obj = _load_json(setup_path)
                if isinstance(setup_obj, dict):
                    setups.append({"path": str(setup_path), "data": setup_obj})
                    extracted = _extract_setup_actions(setup_obj)
                    setup_actions["label_actions"].extend(extracted["label_actions"])
                    setup_actions["persistent_actions"].extend(extracted["persistent_actions"])
                    setup_actions["target_monsters"].extend(extracted["target_monsters"])
            except Exception:
                continue

        # Update global inventory counts
        for a in route_info.get("actions", []):
            used_waypoint_actions[a] = used_waypoint_actions.get(a, 0) + 1

        for item in setup_actions.get("label_actions", []):
            a = item.get("action")
            if isinstance(a, str) and a:
                used_setup_actions[a] = used_setup_actions.get(a, 0) + 1

        for item in setup_actions.get("persistent_actions", []):
            a = item.get("action")
            if isinstance(a, str) and a:
                used_persistent_actions[a] = used_persistent_actions.get(a, 0) + 1

        # Per-script outputs
        script_out = out_dir / script_name
        plot_path = script_out / "route.png"
        save_route_plot(lines, plot_path, title=f"{script_name} (waypoints.in)")

        (script_out / "route_stats.json").write_text(json.dumps(route_info, indent=2), encoding="utf-8")

        # Dump actions usage with context (line numbers)
        action_lines: List[Dict[str, Any]] = []
        for i, w in enumerate(lines):
            if w.kind == "action" and w.action:
                action_lines.append({"line": i + 1, "action": w.action, "raw": w.raw.strip()})
        (script_out / "actions_in_waypoints.json").write_text(json.dumps(action_lines, indent=2), encoding="utf-8")

        (script_out / "setup_actions.json").write_text(json.dumps(setup_actions, indent=2), encoding="utf-8")

        summary["scripts"][script_name] = {
            "waypoints": str(wp_path),
            "has_setups": bool(setups),
            "outputs": {
                "route_png": str(plot_path),
                "route_stats_json": str(script_out / "route_stats.json"),
                "actions_in_waypoints_json": str(script_out / "actions_in_waypoints.json"),
                "setup_actions_json": str(script_out / "setup_actions.json"),
            },
            "counts": route_info.get("counts"),
        }

    # Final inventory
    summary["inventory"]["waypoints_actions_used"] = dict(sorted(used_waypoint_actions.items(), key=lambda kv: (-kv[1], kv[0])))
    summary["inventory"]["setup_label_actions_used"] = dict(sorted(used_setup_actions.items(), key=lambda kv: (-kv[1], kv[0])))
    summary["inventory"]["setup_persistent_actions_used"] = dict(sorted(used_persistent_actions.items(), key=lambda kv: (-kv[1], kv[0])))

    missing_waypoint = sorted([a for a in used_waypoint_actions.keys() if a not in supported_set])
    # Setup actions are usually different function names (conditional_jump_...) and not in waypoint_action.
    missing_setup = sorted([a for a in set(used_setup_actions.keys()) if a not in supported_set])

    summary["inventory"]["missing_waypoint_actions"] = missing_waypoint
    summary["inventory"]["missing_setup_actions"] = missing_setup

    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze scripts-master (waypoints.in + setup*.json) and generate route viewer + inventories.")
    ap.add_argument("--scripts-root", default="scripts-master", help="Path to scripts-master folder")
    ap.add_argument("--out-dir", default="reports/cloudbot", help="Output directory")
    args = ap.parse_args()

    scripts_root = Path(args.scripts_root).resolve()
    out_dir = Path(args.out_dir).resolve()

    if not scripts_root.exists():
        raise SystemExit(f"scripts-root not found: {scripts_root}")

    out_dir.mkdir(parents=True, exist_ok=True)
    summary = analyze_scripts(scripts_root, out_dir)

    (out_dir / "SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote: {out_dir / 'SUMMARY.json'}")


if __name__ == "__main__":
    main()
