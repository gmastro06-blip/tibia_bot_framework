from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class CooldownDecision:
    ready: bool
    remaining_s: float
    reason: str


def _now() -> float:
    # Monotonic avoids issues with system clock changes.
    try:
        return float(time.monotonic())
    except Exception:
        return float(time.time())


def _norm_text(s: str) -> str:
    try:
        return " ".join(str(s or "").strip().lower().split())
    except Exception:
        return str(s or "").strip().lower()


class CooldownManager:
    """Rate-limit / cooldown policy for ActionRequests.

    Goals:
    - Preserve movement fluidity (short throttle, not spam).
    - Respect cooldowns for repeated hotkey presses.
    - Support shared cooldown groups (e.g. 2s offensive group) when action kinds
      are explicitly modeled.

    This is intentionally simple and fail-safe: on any parsing/config error,
    it falls back to conservative defaults.
    """

    def __init__(self) -> None:
        self._last_by_key: dict[str, float] = {}
        self._last_by_group: dict[str, float] = {}

        # Diagnostics: allow disabling cooldown enforcement via env.
        # This is useful for dry-run runs where we want DecisionTrace to
        # reflect planner output rather than rate-limits.
        self.enabled = (self._env("COOLDOWNS_ENABLED") or "1").strip().lower() not in {"0", "false", "no"}

        self.move_min_interval_s = self._env_float("MOVE_MIN_INTERVAL_S", 0.15, lo=0.0, hi=2.0)
        self.hotkey_min_interval_s = self._env_float("HOTKEY_MIN_INTERVAL_S", 0.12, lo=0.0, hi=2.0)

        # Kind-level defaults (seconds). These are conservative and avoid jitter.
        self.kind_cooldowns_s: dict[str, float] = {
            "move": self.move_min_interval_s,
            "heal": self._env_float("HEAL_COOLDOWN_S", 1.0, lo=0.0, hi=10.0),
            "mana": self._env_float("MANA_COOLDOWN_S", 1.0, lo=0.0, hi=10.0),
            "target": self._env_float("TARGET_COOLDOWN_S", 0.20, lo=0.0, hi=5.0),
            "battlelist_target": self._env_float("BATTLELIST_TARGET_COOLDOWN_S", 0.20, lo=0.0, hi=5.0),
            "tool": self._env_float("TOOL_COOLDOWN_S", 0.35, lo=0.0, hi=10.0),
            "loot": self._env_float("LOOT_COOLDOWN_S", 0.35, lo=0.0, hi=10.0),
            "switch": self._env_float("SWITCH_COOLDOWN_S", 0.35, lo=0.0, hi=10.0),
            "maintenance": self._env_float("MAINTENANCE_COOLDOWN_S", 1.0, lo=0.0, hi=30.0),
            "npc_trade": self._env_float("NPC_TRADE_COOLDOWN_S", 0.75, lo=0.0, hi=30.0),
            "depot": self._env_float("DEPOT_COOLDOWN_S", 0.75, lo=0.0, hi=30.0),
            "bank": self._env_float("BANK_COOLDOWN_S", 0.75, lo=0.0, hi=30.0),
            "supplies": self._env_float("SUPPLIES_COOLDOWN_S", 0.75, lo=0.0, hi=30.0),
            "minimap_click_sim": self._env_float("MINIMAP_CLICK_COOLDOWN_S", 0.20, lo=0.0, hi=5.0),
        }

        # Shared cooldown group: offensive spells often share a 2s group.
        # This is only applied to explicit kinds so we don't accidentally slow
        # down potions or targeting.
        self.offensive_group_cd_s = self._env_float("OFFENSIVE_GROUP_COOLDOWN_S", 2.0, lo=0.0, hi=10.0)
        self.offensive_kinds = {
            "spell_attack",
            "attack_spell",
            "offensive_spell",
        }

        # Optional explicit per-action overrides (JSON).
        # Example:
        #   {"heal:F1": 1.0, "tool:CTRL+L": 0.35, "spell_attack:F5": 6.0}
        self.action_overrides_s: dict[str, float] = {}
        raw = (self._env("ACTION_COOLDOWNS_JSON") or "").strip()
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    for k, v in data.items():
                        kk = _norm_text(str(k))
                        try:
                            vv = float(v)
                        except Exception:
                            continue
                        if vv < 0:
                            continue
                        self.action_overrides_s[kk] = vv
            except Exception:
                self.action_overrides_s = {}

        # Optional spell-name cooldowns (supports formula strings).
        # This is only used when action kind is one of offensive_kinds.
        self.spell_name_cooldowns_s: dict[str, float] = {
            # Offensive
            _norm_text("exori gran"): 6.0,
            _norm_text("exori"): 4.0,
            _norm_text("exori min"): 6.0,
            _norm_text("exori mas"): 8.0,
            _norm_text("exori ico"): 2.0,
            _norm_text("exori hur"): 6.0,
            _norm_text("exori gran ico"): 30.0,
            # Healing/support (kept here for future explicit kinds)
            _norm_text("exura ico"): 1.0,
            _norm_text("exura gran ico"): 600.0,
            _norm_text("utura"): 60.0,
            _norm_text("utura gran"): 60.0,
            _norm_text("exana kor"): 2.0,
            # Utility
            _norm_text("exeta res"): 2.0,
            _norm_text("utito tempo"): 2.0,
            _norm_text("utani hur"): 2.0,
            _norm_text("utani tempo hur"): 2.0,
            _norm_text("utamo tempo"): 2.0,
            _norm_text("utito mas sio"): 2.0,
        }

    def _env(self, name: str) -> str:
        try:
            import os

            return str(os.getenv(name, "") or "")
        except Exception:
            return ""

    def _env_float(self, name: str, default: float, *, lo: float, hi: float) -> float:
        raw = (self._env(name) or "").strip()
        if not raw:
            return float(default)
        try:
            val = float(raw)
        except Exception:
            return float(default)
        if val < lo:
            return float(lo)
        if val > hi:
            return float(hi)
        return float(val)

    def _key(self, kind: str, value: str) -> str:
        return _norm_text(f"{kind}:{value}")

    def _group_for(self, kind: str, value: str) -> str | None:
        k = _norm_text(kind)
        if k in self.offensive_kinds:
            return "offensive_group"
        return None

    def _cooldown_s_for(self, kind: str, value: str) -> float:
        k = _norm_text(kind)
        v = str(value or "")

        base = self.move_min_interval_s if k == "move" else self.hotkey_min_interval_s
        cd = float(self.kind_cooldowns_s.get(k, base))
        cd = max(cd, float(base))

        # Explicit per-action override.
        ov = self.action_overrides_s.get(self._key(k, v))
        if ov is not None:
            cd = max(cd, float(ov))

        # Spell-name cooldowns.
        # Supports value encoding: "<spell formula>:<hotkey>" (UI-driven), e.g. "exori gran:F5".
        # We apply per-spell CDs to *any* spell_* kind, but the shared 2s group is
        # only applied to explicit offensive kinds via _group_for().
        if k in self.offensive_kinds or k.startswith("spell_"):
            try:
                spell_part = v.split(":", 1)[0] if ":" in v else v
            except Exception:
                spell_part = v

            vv = _norm_text(spell_part)
            for spell, s_cd in self.spell_name_cooldowns_s.items():
                if spell and spell in vv:
                    cd = max(cd, float(s_cd))
                    break

        return float(cd)

    def decision(self, kind: str, value: str, *, now: float | None = None) -> CooldownDecision:
        if not bool(getattr(self, "enabled", True)):
            return CooldownDecision(ready=True, remaining_s=0.0, reason="disabled")
        t = _now() if now is None else float(now)
        k = _norm_text(kind)
        v = str(value or "")

        cd = self._cooldown_s_for(k, v)
        key = self._key(k, v)

        last_key = float(self._last_by_key.get(key, -1e9))
        rem_key = (last_key + cd) - t

        grp = self._group_for(k, v)
        rem_grp = 0.0
        if grp:
            last_g = float(self._last_by_group.get(grp, -1e9))
            grp_cd = float(self.offensive_group_cd_s)
            rem_grp = (last_g + grp_cd) - t

        remaining = max(0.0, float(rem_key), float(rem_grp))
        if remaining <= 0.0:
            return CooldownDecision(ready=True, remaining_s=0.0, reason="ok")

        reason = "cooldown"
        if rem_grp > rem_key:
            reason = "cooldown_group"
        return CooldownDecision(ready=False, remaining_s=float(remaining), reason=reason)

    def is_ready(self, kind: str, value: str, *, now: float | None = None) -> bool:
        try:
            if not bool(getattr(self, "enabled", True)):
                return True
            return bool(self.decision(kind, value, now=now).ready)
        except Exception:
            return True

    def mark_sent(self, kind: str, value: str, *, now: float | None = None) -> None:
        t = _now() if now is None else float(now)
        k = _norm_text(kind)
        v = str(value or "")

        try:
            self._last_by_key[self._key(k, v)] = float(t)
        except Exception:
            pass

        grp = self._group_for(k, v)
        if grp:
            try:
                self._last_by_group[str(grp)] = float(t)
            except Exception:
                pass


def action_like(obj: Any) -> tuple[str, str]:
    """Best-effort extraction of (kind, value) from ActionRequest-like objects."""

    try:
        kind = str(getattr(obj, "kind", "") or "")
    except Exception:
        kind = ""
    try:
        value = str(getattr(obj, "value", "") or "")
    except Exception:
        value = ""
    return kind, value


def export_state(mgr: CooldownManager) -> Mapping[str, Any]:
    """Debug helper (JSON-friendly). Not used by default."""

    try:
        return {
            "last_by_key": dict(mgr._last_by_key),
            "last_by_group": dict(mgr._last_by_group),
        }
    except Exception:
        return {}
