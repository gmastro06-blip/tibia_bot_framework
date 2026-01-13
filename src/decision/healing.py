from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any


@dataclass(frozen=True)
class HealingDecision:
    heal_hp: bool
    heal_mp: bool
    hp_reason: str = ""
    mp_reason: str = ""


@dataclass
class _HealState:
    armed: bool = False
    last_ts: float = -1.0


class HealingController:
    """Stateful healer with cooldown + hysteresis per stat."""

    def __init__(self) -> None:
        self._state = {
            "hp": _HealState(),
            "mp": _HealState(),
        }

    def _decide(
        self,
        *,
        pct: float | None,
        below: float,
        recover: float,
        cooldown_s: float,
        kind: str,
        now: float,
    ) -> tuple[bool, str]:
        st = self._state[kind]
        reason = ""

        if pct is None:
            st.armed = False
            return False, reason

        recover_eff = max(recover, below)
        if pct < below:
            st.armed = True
        elif pct >= recover_eff:
            st.armed = False

        if not st.armed:
            return False, reason

        reason = f"{kind}={pct:.1f}<{below:.1f}"

        if st.last_ts < 0.0:
            st.last_ts = now
            return True, reason

        since = float(now - st.last_ts)
        if since < max(0.0, cooldown_s):
            return False, reason

        st.last_ts = now
        return True, reason

    def update(self, sig: Any, cfg: Any, *, now: float | None = None) -> HealingDecision:
        """Compute healing intents with hysteresis and cooldown.

        Returns whether to heal HP and/or MP this tick. Keeps internal state.
        """

        if cfg is None:
            return HealingDecision(False, False, "", "")

        try:
            if not bool(getattr(cfg, "enabled", True)):
                return HealingDecision(False, False, "", "")
        except Exception:
            return HealingDecision(False, False, "", "")

        now = time.time() if now is None else float(now)

        try:
            hp_pct = getattr(sig, "hp_pct", None)
        except Exception:
            hp_pct = None
        try:
            mp_pct = getattr(sig, "mp_pct", None)
        except Exception:
            mp_pct = None

        try:
            hp_below = float(getattr(cfg, "hp_below_pct", 0))
        except Exception:
            hp_below = 0.0
        try:
            hp_recover = float(getattr(cfg, "hp_recover_pct", hp_below))
        except Exception:
            hp_recover = hp_below
        try:
            mp_below = float(getattr(cfg, "mp_below_pct", 0))
        except Exception:
            mp_below = 0.0
        try:
            mp_recover = float(getattr(cfg, "mp_recover_pct", mp_below))
        except Exception:
            mp_recover = mp_below
        try:
            cooldown_s = float(getattr(cfg, "cooldown_s", 1.0))
        except Exception:
            cooldown_s = 1.0

        heal_hp, hp_reason = self._decide(
            pct=hp_pct,
            below=hp_below,
            recover=hp_recover,
            cooldown_s=cooldown_s,
            kind="hp",
            now=now,
        )
        heal_mp, mp_reason = self._decide(
            pct=mp_pct,
            below=mp_below,
            recover=mp_recover,
            cooldown_s=cooldown_s,
            kind="mp",
            now=now,
        )

        return HealingDecision(heal_hp=heal_hp, heal_mp=heal_mp, hp_reason=hp_reason, mp_reason=mp_reason)
