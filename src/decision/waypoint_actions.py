from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from action.input_driver import ActionRequest


def _strip(s: str) -> str:
    return (s or "").strip()


def _norm_cmd(s: str) -> str:
    return _strip(s).lower()


@dataclass(frozen=True)
class WaypointActionToken:
    raw: str
    cmd: str
    arg: str = ""


def parse_waypoint_action_tokens(action: Optional[str]) -> List[WaypointActionToken]:
    """Parse a waypoint action string into (cmd,arg) tokens.

    Separators: ';' or '|'
    Args: use ':' (first ':' splits cmd vs arg)

    Examples:
    - "loot;rope" -> [("loot",""), ("rope","")]
    - "note:Hello" -> [("note","Hello")]
    - "wait:500" -> [("wait","500")]
    - "beep:660:120" -> [("beep","660:120")]
    - "require:!low_hp,coords_ok" -> [("require","!low_hp,coords_ok")]
    """

    if not action:
        return []

    raw = str(action)
    for sep in (";", "|"):
        raw = raw.replace(sep, ";")

    out: List[WaypointActionToken] = []
    for part in raw.split(";"):
        p = _strip(part)
        if not p:
            continue
        if ":" in p:
            head, tail = p.split(":", 1)
            out.append(WaypointActionToken(raw=p, cmd=_norm_cmd(head), arg=_strip(tail)))
        else:
            out.append(WaypointActionToken(raw=p, cmd=_norm_cmd(p), arg=""))
    return out


@dataclass(frozen=True)
class WaypointActionConfig:
    quick_loot_hotkey: str = "ctrl+l"
    rope_hotkey: str = "rope"
    shovel_hotkey: str = "shovel"
    weapon_switch: str = "weapon_switch"


def config_from_env() -> WaypointActionConfig:
    return WaypointActionConfig(
        quick_loot_hotkey=os.getenv("ASSIST_QUICK_LOOT", "ctrl+l").strip() or "ctrl+l",
        rope_hotkey=os.getenv("ASSIST_TOOL_ROPE", "rope").strip() or "rope",
        shovel_hotkey=os.getenv("ASSIST_TOOL_SHOVEL", "shovel").strip() or "shovel",
        weapon_switch=os.getenv("ASSIST_ANTITRAP_SWITCH", "weapon_switch").strip() or "weapon_switch",
    )


def build_requests_from_waypoint_action(
    action: Optional[str],
    *,
    committed: bool,
    cfg: Optional[WaypointActionConfig] = None,
) -> List[ActionRequest]:
    """Parse Waypoint.action and turn it into mock ActionRequest(s).

    Supported examples (case-insensitive):
    - "loot" or "quick_loot" -> ActionRequest(kind="loot", value="ctrl+l")
    - "rope" / "shovel" -> ActionRequest(kind="tool", value="rope")
    - "antitrap" / "weapon_switch" -> ActionRequest(kind="switch", value="weapon_switch")
    - "buy_potions" / "sell_potions" -> ActionRequest(kind="trade", value="buy_potions")

    Multiple actions can be separated by ';' or '|'.
    """

    c = cfg or config_from_env()
    note = "committed" if committed else "preview"

    parts = parse_waypoint_action_tokens(action)
    out: List[ActionRequest] = []
    for t in parts:
        cmd = t.cmd
        arg = t.arg

        if cmd in {"loot", "quick_loot", "ql"}:
            out.append(ActionRequest(kind="loot", value=c.quick_loot_hotkey, note=note))
        elif cmd in {"rope", "use_rope"}:
            out.append(ActionRequest(kind="tool", value=c.rope_hotkey, note=note))
        elif cmd in {"shovel", "use_shovel"}:
            out.append(ActionRequest(kind="tool", value=c.shovel_hotkey, note=note))
        elif cmd in {"antitrap", "weapon_switch", "switch_weapon"}:
            out.append(ActionRequest(kind="switch", value=c.weapon_switch, note=note))
        elif cmd in {"sell_potions", "buy_potions"}:
            out.append(ActionRequest(kind="trade", value=cmd, note=note))
        elif cmd in {"wait", "wait_ms"}:
            ms = 0
            try:
                ms = int(float(arg)) if arg else 0
            except Exception:
                ms = 0
            if ms <= 0:
                ms = 500
            out.append(ActionRequest(kind="wait", value=str(ms), note=note))
        elif cmd == "note":
            if arg:
                out.append(ActionRequest(kind="note", value=str(arg), note=note))
        elif cmd == "beep":
            # NOTE: assistant-only side-effect is implemented at the decision layer.
            out.append(ActionRequest(kind="beep", value=str(arg or ""), note=note))
        elif cmd in {"require", "requires"}:
            # Gate evaluation happens in the decision loop; we still emit a trace.
            out.append(ActionRequest(kind="require", value=str(arg or ""), note=note))
        else:
            # Pass-through for custom labels.
            out.append(ActionRequest(kind="waypoint_action", value=str(_strip(t.raw)), note=note))

    return out


def _req_truth(val: bool | None) -> Tuple[bool, bool]:
    """Return (known, truth)."""
    if val is None:
        return False, False
    return True, bool(val)


def evaluate_waypoint_requirements(
    action: Optional[str],
    *,
    low_hp: bool | None = None,
    low_mp: bool | None = None,
    paralyzed: bool | None = None,
    haste_active: bool | None = None,
    utamo_active: bool | None = None,
    hungry: bool | None = None,
    low_cap: bool | None = None,
    low_potions: bool | None = None,
    coords_status: str = "",
    target: str = "",
) -> Tuple[bool, str]:
    """Evaluate `require:` tokens inside a waypoint action string.

    Supported clauses inside `require:<clauses>` (comma-separated):
    - `flag` / `!flag` where flag is one of:
      low_hp, low_mp, paralyzed, haste_active, utamo_active, hungry, low_cap, low_potions
    - `coords_ok` / `!coords_ok` (coords_status == "OK")
    - `has_target` / `!has_target` (target non-empty)
    - `target=<text>` (case-insensitive, substring match)

    Returns (ok, reason). `reason` is empty when ok.
    """

    tokens = parse_waypoint_action_tokens(action)
    req_exprs: List[str] = []
    for t in tokens:
        if t.cmd in {"require", "requires"} and _strip(t.arg):
            req_exprs.append(_strip(t.arg))
    if not req_exprs:
        return True, ""

    # Combine multiple require tokens: all must pass.
    for expr in req_exprs:
        for raw_clause in [c.strip() for c in str(expr).split(",") if c.strip()]:
            neg = raw_clause.startswith("!")
            clause = raw_clause[1:].strip() if neg else raw_clause

            # key=value
            if "=" in clause:
                k, v = clause.split("=", 1)
                k = _norm_cmd(k)
                v = _strip(v)
                if k == "target":
                    ok = (v.lower() in (target or "").lower())
                    if neg:
                        ok = not ok
                    if not ok:
                        return False, f"require:{raw_clause}"
                    continue
                return False, f"require:unknown({raw_clause})"

            key = _norm_cmd(clause)
            if key == "coords_ok":
                ok = str(coords_status or "").strip().upper() == "OK"
                if neg:
                    ok = not ok
                if not ok:
                    return False, f"require:{raw_clause}"
                continue

            if key == "has_target":
                ok = bool((target or "").strip())
                if neg:
                    ok = not ok
                if not ok:
                    return False, f"require:{raw_clause}"
                continue

            mapping = {
                "low_hp": low_hp,
                "low_mp": low_mp,
                "paralyzed": paralyzed,
                "haste_active": haste_active,
                "utamo_active": utamo_active,
                "hungry": hungry,
                "low_cap": low_cap,
                "low_potions": low_potions,
            }
            if key in mapping:
                known, truth = _req_truth(mapping[key])
                if not known:
                    return False, f"require:{raw_clause} (unknown)"
                ok = (not truth) if neg else truth
                if not ok:
                    return False, f"require:{raw_clause}"
                continue

            return False, f"require:unknown({raw_clause})"

    return True, ""
