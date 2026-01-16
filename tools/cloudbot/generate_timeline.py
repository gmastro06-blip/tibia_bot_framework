from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# Permite ejecutar este archivo directamente y aun así importar módulos bajo <repo>/tools/.
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.cloudbot.quick_loot_profile import QuickLootProfile
from tools.cloudbot.refill_profile import RefillProfile
from tools.cloudbot.deposit_profile import DepositProfile


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


def generate_route_timeline(
    script_name: str,
    output_file: Path,
    quick_loot_profile_path: Path | None = None,
    refill_profile_path: Path | None = None,
    deposit_profile_path: Path | None = None,
):
    scripts_root = Path("scripts-master")
    reports_root = Path("reports/cloudbot")

    script_path = scripts_root / script_name
    wp_file = script_path / "waypoints.in"
    if not wp_file.exists():
        print(f"ERROR: waypoints.in no encontrado en {script_path}")
        return

    waypoints = parse_waypoints_in(wp_file)
    reports_path = reports_root / script_name

    # Cargar setup_actions para contexto
    setup_actions = {}
    setup_file = reports_path / "setup_actions.json"
    if setup_file.exists():
        setup_actions = json.loads(setup_file.read_text(encoding="utf-8"))

    # Simular timeline (offline): track targeting/loot flags via actions target_on/off, loot_on/off
    timeline = []
    current_time = 0.0  # segundos
    step_delay = 0.5  # segundos por waypoint (simulado)

    targeting_enabled = False
    loot_enabled = False

    phase = "unknown"  # hunt|refill|deposit|unknown

    ql_profile: QuickLootProfile | None = None
    ql_summary: dict | None = None
    if quick_loot_profile_path is not None:
        try:
            ql_profile = QuickLootProfile.load(quick_loot_profile_path)
            ql_summary = ql_profile.summary()
        except Exception:
            ql_profile = None
            ql_summary = None

    rf_profile: RefillProfile | None = None
    rf_summary: dict | None = None
    if refill_profile_path is not None:
        try:
            rf_profile = RefillProfile.load(refill_profile_path)
            rf_summary = rf_profile.summary()
        except Exception:
            rf_profile = None
            rf_summary = None

    dp_profile: DepositProfile | None = None
    dp_summary: dict | None = None
    if deposit_profile_path is not None:
        try:
            dp_profile = DepositProfile.load(deposit_profile_path)
            dp_summary = dp_profile.summary()
        except Exception:
            dp_profile = None
            dp_summary = None

    for i, w in enumerate(waypoints):
        if w.kind == "label" and w.label:
            lbl = w.label.strip().lower()
            if "refill" in lbl or lbl == "refil":
                phase = "refill"
            elif "deposit" in lbl or "depot" in lbl:
                phase = "deposit"
            elif "hunt" in lbl or "go_hunt" in lbl or lbl == "start":
                phase = "hunt"

        if w.kind == "action" and w.action:
            a = w.action.strip().lower()
            if a == "target_on":
                targeting_enabled = True
            elif a == "target_off":
                targeting_enabled = False
            elif a == "loot_on":
                loot_enabled = True
            elif a == "loot_off":
                loot_enabled = False
            elif a == "refill":
                phase = "refill"

        entry = {
            "time": round(current_time, 1),
            "waypoint_index": i + 1,
            "type": w.kind,
            "description": w.raw.strip(),
            "position": w.pos,
            "action": w.action,
            "label": w.label,
            "state": {
                "targeting": targeting_enabled,
                "loot": loot_enabled,
                "quick_loot": ql_summary,
                "refill": rf_summary,
                "deposit_profile": dp_summary,
                "phase": phase,
                "deposit_step": bool(w.kind == "action" and (w.action or "").strip().lower() == "deposit"),
            },
        }

        # Agregar contexto de setup si es action
        if w.action and w.action in setup_actions.get("label_actions", []):
            for sa in setup_actions["label_actions"]:
                if sa.get("action") == w.action:
                    entry["setup_context"] = sa
                    break

        timeline.append(entry)
        current_time += step_delay

    # Escribir JSON
    output_data = {
        "script": script_name,
        "total_waypoints": len(waypoints),
        "total_time": round(current_time, 1),
        "step_delay": step_delay,
        "quick_loot_profile": {
            "path": str(quick_loot_profile_path) if quick_loot_profile_path is not None else None,
            "summary": ql_summary,
        },
        "refill_profile": {
            "path": str(refill_profile_path) if refill_profile_path is not None else None,
            "summary": rf_summary,
        },
        "deposit_profile": {
            "path": str(deposit_profile_path) if deposit_profile_path is not None else None,
            "summary": dp_summary,
        },
        "timeline": timeline
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(output_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Timeline generado: {output_file}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Genera un timeline detallado de la ruta de un script")
    ap.add_argument("script_name", help="Nombre del script (ej: wasp_ab)")
    ap.add_argument("--output", default="timeline.json", help="Archivo de salida")
    ap.add_argument(
        "--quick-loot-profile",
        default=None,
        help="Ruta a un JSON de perfil Quick Loot (ej: configs/quick_loot_profile.json)",
    )
    ap.add_argument(
        "--refill-profile",
        default=None,
        help="Ruta a un JSON de refill (ej: configs/refill_profile.json)",
    )
    ap.add_argument(
        "--deposit-profile",
        default=None,
        help="Ruta a un JSON de deposit (ej: configs/deposit_profile.json)",
    )
    args = ap.parse_args()

    output_file = Path(args.output)
    qlp = Path(args.quick_loot_profile) if args.quick_loot_profile else None
    rfp = Path(args.refill_profile) if args.refill_profile else None
    dpp = Path(args.deposit_profile) if args.deposit_profile else None
    generate_route_timeline(
        args.script_name,
        output_file,
        quick_loot_profile_path=qlp,
        refill_profile_path=rfp,
        deposit_profile_path=dpp,
    )
