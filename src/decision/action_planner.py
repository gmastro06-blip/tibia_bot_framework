from __future__ import annotations

from typing import Any, Callable, Protocol

from action.input_driver import ActionRequest


class BehaviorTreePlanner(Protocol):
    def tick(
        self,
        *,
        sig: Any,
        healing_cfg: Any,
        heal_hp: bool,
        heal_mp: bool,
        battlelist_target: str,
        battlelist_conf: float | None,
        target_cls: str,
        target_conf: float | None,
        cavebot_next: str,
        cavebot_action: str,
        commit_flag: bool,
        eat_food: bool,
    ) -> list[ActionRequest]: ...  # pragma: no cover


def plan_action_requests(
    *,
    bt_enabled: bool,
    bt_runner: BehaviorTreePlanner | None,
    fallback: Callable[[], list[ActionRequest]],
    sig: Any,
    healing_cfg: Any,
    heal_hp: bool,
    heal_mp: bool,
    battlelist_target: str,
    battlelist_conf: float | None,
    target_cls: str,
    target_conf: float | None,
    cavebot_next: str,
    cavebot_action: str,
    commit_flag: bool,
    eat_food: bool,
    fallback_on_empty: bool = True,
) -> tuple[list[ActionRequest], str]:
    """Plan action requests.

    Policy:
    - If BT is enabled, try BT first.
    - If BT raises OR produces no requests ("no node"), fall back.

    Returns: (requests, source)
    - source: "bt" | "fallback_exception" | "fallback_empty" | "fallback_disabled"
    """

    if bt_enabled and bt_runner is not None:
        try:
            reqs = bt_runner.tick(
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
            )
        except Exception:
            return fallback(), "fallback_exception"

        if reqs:
            return reqs, "bt"
        if fallback_on_empty:
            return fallback(), "fallback_empty"
        return [], "bt"

    return fallback(), "fallback_disabled"
