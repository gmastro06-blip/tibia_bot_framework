from __future__ import annotations

from runtime_config import RuntimeConfig, compute_injection_state


def test_assistant_status_snapshot_is_copy() -> None:
    cfg = RuntimeConfig()

    s0 = cfg.assistant_status_snapshot()
    assert s0.injection_state == "DISABLED"

    cfg.update_assistant_status(
        cavebot_mode="steps",
        step_index=3,
        total_steps=10,
        current_label="start",
        next_step_text="node (1, 2, 7)",
        injection_state="WAITING_CONFIRM",
        injection_reason="no_committed_pulse",
        input_mode="keyboard",
        driver_name="WindowsKeyboardDriver",
        gating_enabled=True,
        advance_pulse_pending=False,
    )

    s1 = cfg.assistant_status_snapshot()
    assert s1.cavebot_mode == "steps"
    assert s1.step_index == 3
    assert s1.total_steps == 10
    assert s1.current_label == "start"
    assert s1.next_step_text == "node (1, 2, 7)"
    assert s1.injection_state == "WAITING_CONFIRM"
    assert s1.injection_reason == "no_committed_pulse"

    # Mutate returned object; internal state must not change.
    s1.cavebot_mode = "pos"
    s1.step_index = 0
    s1.injection_state = "DISABLED"

    s2 = cfg.assistant_status_snapshot()
    assert s2.cavebot_mode == "steps"
    assert s2.step_index == 3
    assert s2.injection_state == "WAITING_CONFIRM"


def test_compute_injection_state_reasoning() -> None:
    assert compute_injection_state(
        input_mode="keyboard",
        driver_name="WindowsKeyboardDriver",
        injection_enabled=True,
        disabled_reason="WATCHDOG_STALE_GS",
        gating_enabled=True,
        advance_pulse_pending=False,
        has_injectable_action=True,
        live_input_armed=True,
        target_window_active=True,
    ) == ("DISABLED", "fail_closed")

    assert compute_injection_state(
        input_mode="log",
        driver_name="MockInputDriver",
        injection_enabled=False,
        disabled_reason="",
        gating_enabled=True,
        advance_pulse_pending=False,
        has_injectable_action=True,
        live_input_armed=False,
        target_window_active=False,
    ) == ("DISABLED", "input_mode=log")

    assert compute_injection_state(
        input_mode="keyboard",
        driver_name="MockInputDriver",
        injection_enabled=False,
        disabled_reason="",
        gating_enabled=True,
        advance_pulse_pending=False,
        has_injectable_action=True,
        live_input_armed=False,
        target_window_active=False,
    ) == ("DISABLED", "not_armed")

    assert compute_injection_state(
        input_mode="keyboard",
        driver_name="WindowsKeyboardDriver",
        injection_enabled=True,
        disabled_reason="",
        gating_enabled=True,
        advance_pulse_pending=False,
        has_injectable_action=False,
        live_input_armed=True,
        target_window_active=True,
    ) == ("DISABLED", "no_action")

    assert compute_injection_state(
        input_mode="keyboard",
        driver_name="WindowsKeyboardDriver",
        injection_enabled=True,
        disabled_reason="",
        gating_enabled=True,
        advance_pulse_pending=True,
        has_injectable_action=True,
        live_input_armed=True,
        target_window_active=True,
    ) == ("ARMED", "advance_pulse_pending")

    assert compute_injection_state(
        input_mode="keyboard",
        driver_name="WindowsKeyboardDriver",
        injection_enabled=True,
        disabled_reason="",
        gating_enabled=True,
        advance_pulse_pending=False,
        has_injectable_action=True,
        live_input_armed=True,
        target_window_active=True,
    ) == ("WAITING_CONFIRM", "no_committed_pulse")
