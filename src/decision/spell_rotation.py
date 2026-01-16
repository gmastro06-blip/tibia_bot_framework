from __future__ import annotations

from typing import Mapping


def _norm(s: str) -> str:
    try:
        return " ".join(str(s or "").strip().lower().split())
    except Exception:
        return str(s or "").strip().lower()


# Canonical spell lists (used only for UI-driven hotkeys -> ActionRequests).
# If a spell has no configured hotkey, it is skipped.
OFFENSIVE_SPELLS: list[str] = [
    "exori gran",
    "exori",
    "exori min",
    "exori mas",
    "exori ico",
    "exori hur",
    "exori gran ico",
]

SUPPORT_SPELLS: list[str] = [
    "exura ico",
    "exura gran ico",
    "utura",
    "utura gran",
    "exana kor",
]

UTILITY_SPELLS: list[str] = [
    "exeta res",
    "utito tempo",
    "utani hur",
    "utani tempo hur",
    "utamo tempo",
    "utito mas sio",
]


def build_spell_requests(
    *,
    hotkeys: Mapping[str, str] | None,
    enabled: bool,
    has_target: bool,
    committed: bool,
) -> list[object]:
    """Build ActionRequest-like objects for configured spells.

    We keep return type as list[object] to avoid import cycles; caller runs this
    in the decision thread where ActionRequest is available.

    Value encoding:
      "<spell formula>:<hotkey>"  (e.g. "exori gran:F5")

    Cooldowns:
    - The CooldownManager parses the spell formula portion.
    - Offensive spells share the 2s group cooldown via kind="spell_attack".
    """

    if not enabled:
        return []

    hk = hotkeys or {}
    idx: dict[str, str] = {}
    try:
        for k, v in hk.items():
            key = _norm(str(k))
            val = str(v or "").strip().upper()
            if key:
                idx[key] = val
    except Exception:
        idx = {}

    # Late import to avoid hard dependency when running isolated tools.
    try:
        from action.input_driver import ActionRequest
    except Exception:
        return []

    out: list[object] = []
    note = "committed" if committed else "preview"

    def emit(kind: str, spell: str) -> None:
        key = _norm(spell)
        hotkey = str(idx.get(key, "") or "").strip().upper()
        if not hotkey:
            return
        out.append(ActionRequest(kind=kind, value=f"{spell}:{hotkey}", note=note))

    # Offensive: only makes sense if we have a target.
    if has_target:
        for s in OFFENSIVE_SPELLS:
            emit("spell_attack", s)

    # Support/utility: allowed even without target (kept conservative by cooldowns).
    for s in SUPPORT_SPELLS:
        emit("spell_support", s)
    for s in UTILITY_SPELLS:
        emit("spell_utility", s)

    return out
