from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import py_trees

from action.input_driver import ActionRequest
from decision.waypoint_actions import build_requests_from_waypoint_action


@dataclass(frozen=True)
class BTInputs:
    sig: Any
    healing_cfg: Any
    heal_hp: bool
    heal_mp: bool
    battlelist_target: str
    battlelist_conf: float | None
    target_cls: str
    target_conf: float | None
    cavebot_next: str
    cavebot_action: str
    commit_flag: bool
    eat_food: bool


class _BB:
    # Blackboard keys (keep stable strings for debugging)
    # NOTE: py_trees' Blackboard.set() treats dot-separated names as nested
    # attributes and will raise if the root doesn't exist. Keep keys flat.
    inputs = "bt_inputs"
    out_requests = "bt_out_requests"


class EnsureUIHealthy(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="EnsureUIHealthy")
        self.bb = py_trees.blackboard.Blackboard()

    def update(self) -> py_trees.common.Status:
        # Always reset planned requests each tick (prevents stale accumulation).
        self.bb.set(_BB.out_requests, [])
        return py_trees.common.Status.SUCCESS


class EnsureSafe(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="EnsureSafe")
        self.bb = py_trees.blackboard.Blackboard()

    def update(self) -> py_trees.common.Status:
        # Policy hook (future): allow gating committed actions based on signals.
        # Today this is a no-op to preserve existing semantics.
        return py_trees.common.Status.SUCCESS


class HealIfNeeded(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="HealIfNeeded")
        self.bb = py_trees.blackboard.Blackboard()

    def update(self) -> py_trees.common.Status:
        inp: BTInputs | None = self.bb.get(_BB.inputs)
        if inp is None:
            return py_trees.common.Status.SUCCESS

        try:
            enabled = bool(getattr(inp.healing_cfg, "enabled", False))
            hp_flag = bool(getattr(inp, "heal_hp", False))
            mp_flag = bool(getattr(inp, "heal_mp", False))
            triggered = bool(getattr(inp.sig, "healing_trigger", False)) if inp.sig is not None else False
            explicit = hp_flag or mp_flag

            if enabled and (explicit or triggered):
                act_hp = (getattr(inp.healing_cfg, "hp_action", "") or getattr(inp.healing_cfg, "action", "") or "").strip() or "heal"
                act_mp = (getattr(inp.healing_cfg, "mp_action", "") or getattr(inp.healing_cfg, "action", "") or "").strip() or "heal"
                reqs: list[ActionRequest] = list(self.bb.get(_BB.out_requests) or [])

                if explicit:
                    if hp_flag:
                        reqs.append(ActionRequest(kind="heal", value=str(act_hp), note="preview"))
                    if mp_flag:
                        reqs.append(ActionRequest(kind="heal", value=str(act_mp), note="preview"))
                elif triggered:
                    # Legacy fallback: single heal when only healing_trigger is provided.
                    reqs.append(ActionRequest(kind="heal", value=str(act_hp), note="preview"))

                self.bb.set(_BB.out_requests, reqs)
        except Exception:
            pass

        return py_trees.common.Status.SUCCESS


class _PlanCavebotMove(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="PlanCavebotMove")
        self.bb = py_trees.blackboard.Blackboard()

    def update(self) -> py_trees.common.Status:
        inp: BTInputs | None = self.bb.get(_BB.inputs)
        if inp is None:
            return py_trees.common.Status.SUCCESS

        try:
            if inp.cavebot_next in {"north", "south", "east", "west"}:
                note = "committed" if bool(inp.commit_flag) else "preview"
                reqs: list[ActionRequest] = list(self.bb.get(_BB.out_requests) or [])
                reqs.append(ActionRequest(kind="move", value=str(inp.cavebot_next), note=note))
                self.bb.set(_BB.out_requests, reqs)
        except Exception:
            pass

        return py_trees.common.Status.SUCCESS


class AcquireTargetFromBattlelist(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="AcquireTargetFromBattlelist")
        self.bb = py_trees.blackboard.Blackboard()

    @staticmethod
    def _enabled() -> bool:
        raw = (os.getenv("BT_TARGET_ENABLED", "1") or "1").strip().lower()
        return raw in {"1", "true", "yes"}

    @staticmethod
    def _min_conf() -> float:
        try:
            return float(os.getenv("BT_TARGET_MIN_CONF", "0.0").strip() or "0.0")
        except Exception:
            return 0.0

    def update(self) -> py_trees.common.Status:
        if not self._enabled():
            return py_trees.common.Status.SUCCESS

        inp: BTInputs | None = self.bb.get(_BB.inputs)
        if inp is None:
            return py_trees.common.Status.SUCCESS

        try:
            # Prefer battlelist target when available.
            bl = str(getattr(inp, "battlelist_target", "") or "").strip().lower()
            cls = bl
            conf = getattr(inp, "battlelist_conf", None)
            if not cls or cls == "none":
                cls = str(getattr(inp, "target_cls", "") or "").strip().lower()
                conf = getattr(inp, "target_conf", None)
                if not cls or cls == "none":
                    return py_trees.common.Status.SUCCESS

            if conf is not None and float(conf) < float(self._min_conf()):
                return py_trees.common.Status.SUCCESS

            reqs: list[ActionRequest] = list(self.bb.get(_BB.out_requests) or [])
            reqs.append(ActionRequest(kind="target", value=cls, note="preview"))
            self.bb.set(_BB.out_requests, reqs)
        except Exception:
            pass

        return py_trees.common.Status.SUCCESS


class NavigateToWaypoint(_PlanCavebotMove):
    def __init__(self) -> None:
        super().__init__()
        self.name = "NavigateToWaypoint"


class _PlanBeepOnTargetChange(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="PlanBeepOnTargetChange")
        self.bb = py_trees.blackboard.Blackboard()
        self._last_cls: str = ""
        self._last_beep_ts: float = 0.0
        self._allowed_cache: set[str] | None = None
        self._rules_cache = None
        self._matcher_cache = None

    @staticmethod
    def _enabled() -> bool:
        raw = (os.getenv("BT_BEEP_ON_TARGET", "0") or "0").strip().lower()
        return raw in {"1", "true", "yes"}

    @staticmethod
    def _allowed_classes() -> set[str]:
        raw = (os.getenv("BT_BEEP_TARGET_CLASSES", "") or "").strip().lower()
        if not raw:
            return set()
        return {s.strip() for s in raw.split(",") if s.strip()}

    @staticmethod
    def _use_bestiary_priority() -> bool:
        raw = (os.getenv("BT_BEEP_TARGET_USE_BESTIARY", "1") or "1").strip().lower()
        return raw in {"1", "true", "yes"}

    @staticmethod
    def _tiered() -> bool:
        # Only matters when BT_BEEP_ON_TARGET=1.
        raw = (os.getenv("BT_BEEP_TARGET_TIERED", "1") or "1").strip().lower()
        return raw in {"1", "true", "yes"}

    @staticmethod
    def _only_priority() -> bool:
        raw = (os.getenv("BT_BEEP_TARGET_ONLY_PRIORITY", "0") or "0").strip().lower()
        return raw in {"1", "true", "yes"}

    @staticmethod
    def _cooldown_s() -> float:
        """Minimum time between beeps (seconds)."""
        try:
            v = float(os.getenv("BT_BEEP_TARGET_COOLDOWN_S", "0.8").strip() or "0.8")
        except Exception:
            v = 0.8
        return max(0.0, float(v))

    @staticmethod
    def _bestiary_enabled() -> bool:
        return (os.getenv("BESTIARY_ENABLED", "") or "").strip().lower() in {"1", "true", "yes"}

    def _bestiary_rules(self):
        if self._rules_cache is not None:
            return self._rules_cache

        if not self._bestiary_enabled():
            self._rules_cache = None
            return None

        try:
            from pathlib import Path

            from bestiary.matcher import BestiaryRules

            repo_root = Path(__file__).resolve().parents[2]
            cfg_path = (os.getenv("BESTIARY_CONFIG_PATH", "") or "").strip() or str(
                repo_root / "configs" / "bestiary_match.yaml"
            )
            self._rules_cache = BestiaryRules.load_yaml(cfg_path)
        except Exception:
            self._rules_cache = None
        return self._rules_cache

    def _bestiary_matcher(self):
        if self._matcher_cache is not None:
            return self._matcher_cache

        if not self._bestiary_enabled():
            self._matcher_cache = None
            return None

        try:
            from pathlib import Path

            from bestiary.matcher import BestiaryMatcher

            repo_root = Path(__file__).resolve().parents[2]
            reg_path = (os.getenv("BESTIARY_REGISTRY_PATH", "") or "").strip() or str(
                repo_root / "data" / "creatures_registry.json"
            )
            cfg_path = (os.getenv("BESTIARY_CONFIG_PATH", "") or "").strip() or str(
                repo_root / "configs" / "bestiary_match.yaml"
            )
            corr_path = (os.getenv("BESTIARY_OCR_CORRECTIONS_PATH", "") or "").strip() or str(
                repo_root / "configs" / "ocr_corrections.json"
            )
            self._matcher_cache = BestiaryMatcher.from_files(
                registry_path=reg_path,
                config_path=cfg_path,
                ocr_corrections_path=corr_path,
            )
        except Exception:
            self._matcher_cache = None
        return self._matcher_cache

    def _beep_value_for_target(self, cls: str) -> str:
        if not self._tiered():
            return "target_change"

        # If bestiary is enabled, classify based on fuzzy match tier.
        try:
            if self._bestiary_enabled():
                m = self._bestiary_matcher()
                if m is None:
                    return "target_change"
                r = m.match(cls)
                if r.name_key is None:
                    return "target_unknown"
                if bool(getattr(r, "ambiguous", False)):
                    return "target_ambiguous"
                t = str(getattr(r, "tier", "") or "")
                if t == "reliable":
                    return "target_reliable"
                if t == "probable":
                    return "target_probable"
                return "target_unknown"
        except Exception:
            return "target_change"

        return "target_change"

    def _allowed_classes_effective(self) -> set[str]:
        """Return allowed classes for beeping.

        Priority order:
        1) Explicit BT_BEEP_TARGET_CLASSES
        2) Bestiary rules.priority (when BESTIARY_ENABLED=1)
        3) Empty set => allow all
        """

        if self._allowed_cache is not None:
            return set(self._allowed_cache)

        explicit = self._allowed_classes()
        if explicit:
            self._allowed_cache = set(explicit)
            return set(self._allowed_cache)

        if not self._use_bestiary_priority():
            self._allowed_cache = set()
            return set()

        bestiary_enabled = (os.getenv("BESTIARY_ENABLED", "") or "").strip().lower() in {"1", "true", "yes"}
        if not bestiary_enabled:
            self._allowed_cache = set()
            return set()

        try:
            from pathlib import Path

            from bestiary.matcher import BestiaryRules

            repo_root = Path(__file__).resolve().parents[2]
            cfg_path = (os.getenv("BESTIARY_CONFIG_PATH", "") or "").strip() or str(
                repo_root / "configs" / "bestiary_match.yaml"
            )
            rules = BestiaryRules.load_yaml(cfg_path)
            pr = getattr(rules, "priority", None)
            if pr:
                self._allowed_cache = {str(x).strip().lower() for x in pr if str(x).strip()}
                return set(self._allowed_cache)
        except Exception:
            pass

        self._allowed_cache = set()
        return set()

    def update(self) -> py_trees.common.Status:
        if not self._enabled():
            return py_trees.common.Status.SUCCESS

        inp: BTInputs | None = self.bb.get(_BB.inputs)
        if inp is None:
            return py_trees.common.Status.SUCCESS

        try:
            cls = str(getattr(inp, "target_cls", "") or "").strip().lower()
        except Exception:
            cls = ""

        if not cls or cls == "none":
            # Reset so the next real target triggers a beep.
            self._last_cls = ""
            return py_trees.common.Status.SUCCESS

        # Bestiary ignore: never beep on ignored creatures.
        try:
            rules = self._bestiary_rules()
            if rules is not None and cls in getattr(rules, "ignore", frozenset()):
                return py_trees.common.Status.SUCCESS
        except Exception:
            pass

        allowed = self._allowed_classes_effective()
        if allowed and cls not in allowed:
            return py_trees.common.Status.SUCCESS

        if self._only_priority() and allowed and cls not in allowed:
            return py_trees.common.Status.SUCCESS

        if cls == self._last_cls:
            return py_trees.common.Status.SUCCESS

        self._last_cls = cls

        # Rate limit beeps to avoid spamming when detections flicker.
        try:
            cd = float(self._cooldown_s())
            if cd > 0.0:
                now = float(time.time())
                if (now - float(self._last_beep_ts or 0.0)) < cd:
                    return py_trees.common.Status.SUCCESS
                self._last_beep_ts = now
        except Exception:
            pass
        try:
            reqs: list[ActionRequest] = list(self.bb.get(_BB.out_requests) or [])
            reqs.append(ActionRequest(kind="beep", value=self._beep_value_for_target(cls), note="preview"))
            self.bb.set(_BB.out_requests, reqs)
        except Exception:
            pass

        return py_trees.common.Status.SUCCESS


class _PlanWaypointAction(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="PlanWaypointAction")
        self.bb = py_trees.blackboard.Blackboard()

    def update(self) -> py_trees.common.Status:
        inp: BTInputs | None = self.bb.get(_BB.inputs)
        if inp is None:
            return py_trees.common.Status.SUCCESS

        try:
            if inp.cavebot_action:
                reqs: list[ActionRequest] = list(self.bb.get(_BB.out_requests) or [])
                reqs.extend(
                    build_requests_from_waypoint_action(inp.cavebot_action, committed=bool(inp.commit_flag))
                )
                self.bb.set(_BB.out_requests, reqs)
        except Exception:
            pass

        return py_trees.common.Status.SUCCESS


class _PlanFood(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="PlanFood")
        self.bb = py_trees.blackboard.Blackboard()

    def update(self) -> py_trees.common.Status:
        inp: BTInputs | None = self.bb.get(_BB.inputs)
        if inp is None:
            return py_trees.common.Status.SUCCESS

        try:
            if bool(inp.eat_food):
                reqs: list[ActionRequest] = list(self.bb.get(_BB.out_requests) or [])
                reqs.append(ActionRequest(kind="maintenance", value="eat_food", note="preview"))
                self.bb.set(_BB.out_requests, reqs)
        except Exception:
            pass

        return py_trees.common.Status.SUCCESS


class BehaviorTreeRunner:
    """Assistant-only behavior tree.

    Produces ActionRequest objects (no input injection).

    Enable/disable via env:
    - BT_ENABLED (default: 1)
    """

    def __init__(self) -> None:
        root = py_trees.composites.Sequence(name="BT", memory=False)
        root.add_children([
            EnsureUIHealthy(),
            EnsureSafe(),
            HealIfNeeded(),
            AcquireTargetFromBattlelist(),
            _PlanBeepOnTargetChange(),
            NavigateToWaypoint(),
            _PlanWaypointAction(),
            _PlanFood(),
        ])
        self.tree = py_trees.trees.BehaviourTree(root)
        self.bb = py_trees.blackboard.Blackboard()

    @staticmethod
    def enabled_from_env() -> bool:
        raw = (os.getenv("BT_ENABLED", "1") or "1").strip().lower()
        return raw in {"1", "true", "yes"}

    def tick(
        self,
        *,
        sig: Any,
        healing_cfg: Any,
        heal_hp: bool = False,
        heal_mp: bool = False,
        battlelist_target: str = "",
        battlelist_conf: float | None = None,
        target_cls: str,
        target_conf: float | None,
        cavebot_next: str,
        cavebot_action: str,
        commit_flag: bool,
        eat_food: bool,
    ) -> list[ActionRequest]:
        self.bb.set(
            _BB.inputs,
            BTInputs(
                sig=sig,
                healing_cfg=healing_cfg,
                heal_hp=bool(heal_hp),
                heal_mp=bool(heal_mp),
                battlelist_target=str(battlelist_target or ""),
                battlelist_conf=(float(battlelist_conf) if battlelist_conf is not None else None),
                target_cls=str(target_cls or ""),
                target_conf=(float(target_conf) if target_conf is not None else None),
                cavebot_next=str(cavebot_next or ""),
                cavebot_action=str(cavebot_action or ""),
                commit_flag=bool(commit_flag),
                eat_food=bool(eat_food),
            ),
        )

        try:
            self.tree.tick()
        except Exception:
            # Fail-safe: don't crash decision thread.
            pass

        out = self.bb.get(_BB.out_requests)
        if isinstance(out, list):
            try:
                return [r for r in out if isinstance(r, ActionRequest)]
            except Exception:
                return []
        return []
