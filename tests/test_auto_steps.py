from decision.auto_steps import AutoStepFallback


def test_auto_steps_activates_on_red_sustained() -> None:
    auto = AutoStepFallback(activate_s=1.0, recover_s=1.0)

    use_steps, reason = auto.update("red", has_coords=True, base_steps=False, now=0.0)
    assert not use_steps and reason == ""

    use_steps, reason = auto.update("red", has_coords=True, base_steps=False, now=0.5)
    assert not use_steps and reason == ""

    use_steps, reason = auto.update("red", has_coords=True, base_steps=False, now=1.2)
    assert use_steps and reason.startswith("auto_steps:red")


def test_auto_steps_recovers_on_amber_sustained() -> None:
    auto = AutoStepFallback(activate_s=0.5, recover_s=1.0)

    # Activate
    auto.update("red", has_coords=True, base_steps=False, now=0.0)
    auto.update("red", has_coords=True, base_steps=False, now=0.6)

    use_steps, reason = auto.update("amber", has_coords=True, base_steps=False, now=0.8)
    assert use_steps and reason

    use_steps, reason = auto.update("amber", has_coords=True, base_steps=False, now=1.9)
    assert not use_steps and reason == ""


def test_auto_steps_triggers_when_no_coords() -> None:
    auto = AutoStepFallback(activate_s=1.0, recover_s=1.0)

    use_steps, reason = auto.update("", has_coords=False, base_steps=False, now=0.0)
    assert not use_steps and reason == ""

    use_steps, reason = auto.update("", has_coords=False, base_steps=False, now=1.6)
    assert use_steps and reason.startswith("auto_steps:no_coords")


def test_base_steps_short_circuit_resets_state() -> None:
    auto = AutoStepFallback(activate_s=0.1, recover_s=0.1)
    auto.update("red", has_coords=True, base_steps=False, now=0.0)
    auto.update("red", has_coords=True, base_steps=False, now=0.2)

    use_steps, reason = auto.update("green", has_coords=True, base_steps=True, now=0.3)
    assert use_steps and reason == ""
    # After base_steps reset, it should not stay active when re-evaluated with good signal.
    use_steps, reason = auto.update("green", has_coords=True, base_steps=False, now=0.4)
    assert not use_steps and reason == ""
