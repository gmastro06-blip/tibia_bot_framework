from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any

from action.input_driver import ActionRequest


@dataclass
class AutoTargetConfig:
    enabled: bool = False
    follow_on_enable: bool = True
    follow_distance_tiles: int = 1
    retarget_if_lost_ms: int = 800
    retarget_if_hp_zero: bool = True
    prefer_nearest: bool = True
    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)
    # NOTE: battlelist.py stabilizes confidence as a relative vote share (0..1),
    # which tends to be much lower than raw OCR confidence. Default low so we
    # don't filter everything in real runs.
    min_confidence: float = 0.1


@dataclass
class AutoTargetState:
    current_target_name: str = ""
    last_seen_ts: float = 0.0
    follow_active: bool = False
    last_retarget_ts: float = 0.0


def _norm_name(s: str) -> str:
    try:
        return " ".join(str(s or "").strip().lower().split())
    except Exception:
        return ""


def _pick_display_name(entry: dict[str, Any]) -> str:
    try:
        s = str(entry.get("name_display") or "").strip()
        if s:
            return s
    except Exception:
        pass
    try:
        s = str(entry.get("name_raw") or "").strip()
        if s:
            return s
    except Exception:
        pass
    try:
        s = str(entry.get("name_norm") or "").strip()
        if s:
            return s
    except Exception:
        pass
    return ""


def _entry_conf(entry: dict[str, Any]) -> float | None:
    for k in ("conf", "confidence"):
        try:
            if k in entry:
                v = entry.get(k)
                if v is not None:
                    return float(v)
        except Exception:
            return None
    return None


def _entry_hp(entry: dict[str, Any]) -> float | None:
    # Best-effort: not all sources provide this.
    for k in ("hp_pct", "hp", "hp_current"):
        try:
            if k in entry:
                v = entry.get(k)
                if v is not None:
                    return float(v)
        except Exception:
            return None
    return None


def select_target(monsters: list[dict[str, Any]], cfg: AutoTargetConfig) -> dict[str, Any] | None:
    """Select best target from a list of monsters.

    Inputs are expected to be JSON-friendly dicts.
    """

    wl = {_norm_name(x) for x in (cfg.whitelist or []) if _norm_name(x)}
    bl = {_norm_name(x) for x in (cfg.blacklist or []) if _norm_name(x)}

    filtered: list[dict[str, Any]] = []
    for m in monsters or []:
        if not isinstance(m, dict):
            continue

        name = _pick_display_name(m)
        name_norm = _norm_name(name)
        if not name_norm:
            continue

        if wl and name_norm not in wl:
            continue
        if bl and name_norm in bl:
            continue

        conf = _entry_conf(m)
        if conf is not None:
            try:
                if float(conf) < float(cfg.min_confidence):
                    continue
            except Exception:
                continue

        hp = _entry_hp(m)
        if hp is not None:
            try:
                if float(hp) <= 0.0:
                    continue
            except Exception:
                pass

        filtered.append(m)

    if not filtered:
        return None

    # Prefer nearest if a distance signal exists.
    if bool(cfg.prefer_nearest):
        dist_keys = ("distance", "dist", "distance_tiles")
        with_dist: list[tuple[float, int, dict[str, Any]]] = []
        for i, m in enumerate(filtered):
            d = None
            for k in dist_keys:
                try:
                    if k in m:
                        v = m.get(k)
                        if v is not None:
                            d = float(v)
                        break
                except Exception:
                    d = None
            if d is None:
                continue
            with_dist.append((float(d), int(i), m))

        if with_dist:
            with_dist.sort(key=lambda t: (t[0], t[1]))
            return with_dist[0][2]

    # Stable order fallback: avoid target flip.
    return filtered[0]


class AutoTargetingController:
    """Auto target selection + follow toggle (assistant-only).

    This controller only emits ActionRequest objects; it does not inject inputs.
    """

    def __init__(self) -> None:
        self.state = AutoTargetState()
        self._last_enabled: bool = False
        self.last_retarget_reason: str = ""

    def reset(self) -> None:
        self.state = AutoTargetState()
        self._last_enabled = False
        self.last_retarget_reason = ""

    def snapshot(self) -> AutoTargetState:
        return AutoTargetState(**self.state.__dict__)

    def tick(
        self,
        gamestate: object,
        *,
        now: float | None = None,
        cfg: AutoTargetConfig | None = None,
        block_actions: bool = False,
    ) -> list[ActionRequest]:
        now = time.time() if now is None else float(now)
        cfg = AutoTargetConfig() if cfg is None else cfg

        actions: list[ActionRequest] = []

        prev_enabled = bool(self._last_enabled)

        enabled = bool(getattr(cfg, "enabled", False))
        if enabled and not prev_enabled:
            # Rising edge: enable follow if configured.
            if bool(getattr(cfg, "follow_on_enable", True)):
                self.state.follow_active = True
        if (not enabled) and prev_enabled:
            # Falling edge: stop follow + clear state.
            self.state.follow_active = False
            self.state.current_target_name = ""
            self.state.last_seen_ts = 0.0
            self.last_retarget_reason = "disabled"
            actions.append(ActionRequest(kind="stop_follow", value="", note="preview"))
            actions.append(ActionRequest(kind="clear_target", value="", note="preview"))

        self._last_enabled = enabled

        if not enabled:
            # Disabled: state already handled above.
            return [] if block_actions else actions

        # Read monsters list from GameState (battlelist is the stable/available source).
        monsters: list[dict[str, Any]] = []
        try:
            raw = getattr(gamestate, "battlelist_entries", None)
            if isinstance(raw, list):
                monsters = [dict(x) for x in raw if isinstance(x, dict)]
        except Exception:
            monsters = []

        # Update last_seen if current target is present.
        cur = _norm_name(self.state.current_target_name)
        cur_visible = False
        cur_dead = False
        if cur:
            for m in monsters:
                name = _norm_name(_pick_display_name(m))
                if not name:
                    continue
                if name == cur:
                    cur_visible = True
                    hp = _entry_hp(m)
                    if hp is not None and bool(getattr(cfg, "retarget_if_hp_zero", True)):
                        try:
                            cur_dead = float(hp) <= 0.0
                        except Exception:
                            cur_dead = False
                    break

        if cur_visible and not cur_dead:
            self.state.last_seen_ts = float(now)
        else:
            # If target is dead (explicit hp<=0), clear immediately.
            if cur and cur_dead:
                self.last_retarget_reason = "dead"
                actions.append(ActionRequest(kind="clear_target", value="", note="preview"))
                self.state.current_target_name = ""
                self.state.last_seen_ts = 0.0

        # Retarget policy: if no target, or target lost for too long.
        need_retarget = False
        if not _norm_name(self.state.current_target_name):
            need_retarget = True
            if not self.last_retarget_reason:
                self.last_retarget_reason = "none"
        else:
            if not cur_visible:
                # If we haven't seen it for long enough, retarget.
                try:
                    lost_s = max(0.0, float(getattr(cfg, "retarget_if_lost_ms", 800)) / 1000.0)
                except Exception:
                    lost_s = 0.8
                since = float(now - float(self.state.last_seen_ts or 0.0))
                if self.state.last_seen_ts <= 0.0 or since >= lost_s:
                    need_retarget = True
                    self.last_retarget_reason = "lost"

        target_changed = False
        if need_retarget:
            chosen = select_target(monsters, cfg)
            if chosen is not None:
                new_name = _pick_display_name(chosen)
                if _norm_name(new_name) and _norm_name(new_name) != _norm_name(self.state.current_target_name):
                    self.state.current_target_name = str(new_name)
                    self.state.last_seen_ts = float(now)
                    self.state.last_retarget_ts = float(now)
                    target_changed = True
                    if self.last_retarget_reason in {"", "none"}:
                        self.last_retarget_reason = "retarget"

                    # Emit an actionable target request (maps to the configured
                    # target hotkey) while keeping the chosen name for UI/telemetry.
                    actions.append(ActionRequest(kind="target", value=str(new_name), note="preview"))

        # Follow behavior: treat as a mode toggle; emit only when enabled or target changed.
        if bool(self.state.follow_active) and _norm_name(self.state.current_target_name):
            if (enabled and not prev_enabled) or target_changed:
                payload = {
                    "target": str(self.state.current_target_name),
                    "dist": int(max(0, int(getattr(cfg, "follow_distance_tiles", 1) or 1))),
                }
                actions.append(ActionRequest(kind="follow_target", value=json.dumps(payload), note="preview"))

        return [] if block_actions else actions
