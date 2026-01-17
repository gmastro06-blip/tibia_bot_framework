from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast


class _Var:
    def __init__(self, value: str = "") -> None:
        self._v = value

    def get(self) -> str:
        return self._v

    def set(self, v: str) -> None:
        self._v = str(v)


@dataclass
class _Step:
    kind: str = "node"
    x: int | None = None
    y: int | None = None
    z: int | None = None
    name: str = ""
    params: dict | None = None
    comment: str = ""
    enabled: bool = True


def test_route_save_calls_apply_and_writes(tmp_path) -> None:
    import run_bot_ui

    class Dummy:
        def __init__(self) -> None:
            self._repo_root = tmp_path
            self.route_steps: list[_Step] = [_Step(kind="node")]
            self.route_path_var: _Var = _Var(str(tmp_path / "route"))
            self.route_status_var: _Var = _Var("")
            self._applied = False

        def _route_apply_form(self) -> None:
            self._applied = True
            # Simulate user edited the current step in the form.
            self.route_steps[0].kind = "action"
            self.route_steps[0].name = "rope"

        def _expand_move_macros(self, steps):
            return list(steps), []

        def _serialize_waypoints(self, steps) -> str:
            # Minimal serialization to assert that the saved file reflects the applied form.
            s0 = steps[0]
            return f"{getattr(s0, 'kind', '')}:{getattr(s0, 'name', '')}\n"

    d = Dummy()
    (tmp_path / "route").mkdir(parents=True, exist_ok=True)

    run_bot_ui.BotUI._route_save(cast(Any, d))

    assert d._applied is True, "Expected _route_apply_form() to be called before saving"

    wp_path = tmp_path / "route" / "waypoints.in"
    assert wp_path.exists(), "Expected waypoints.in to be written"
    assert wp_path.read_text(encoding="utf-8") == "action:rope\n"


def test_route_save_empty_route_sets_status_and_does_not_write(tmp_path) -> None:
    import run_bot_ui

    class Dummy:
        def __init__(self) -> None:
            self._repo_root = tmp_path
            self.route_steps: list[_Step] = []
            self.route_path_var: _Var = _Var(str(tmp_path / "route"))
            self.route_status_var: _Var = _Var("")

        def _route_apply_form(self) -> None:
            # Should still be safe to call.
            return

        def _expand_move_macros(self, steps):
            return list(steps), []

        def _serialize_waypoints(self, steps) -> str:
            return ""

    d = Dummy()
    (tmp_path / "route").mkdir(parents=True, exist_ok=True)

    run_bot_ui.BotUI._route_save(cast(Any, d))

    assert d.route_status_var.get() == "Ruta vacía"
    wp_path = tmp_path / "route" / "waypoints.in"
    assert not wp_path.exists(), "Should not write waypoints.in for an empty route"
