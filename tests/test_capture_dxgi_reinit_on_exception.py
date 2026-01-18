from __future__ import annotations

import pytest


def test_capture_reinit_needed_on_exception_streak() -> None:
    import main

    assert main._capture_reinit_needed(exc_streak=3, last_ok_age_s=None) is True
    assert main._capture_reinit_needed(exc_streak=2, last_ok_age_s=None) is False


def test_capture_reinit_needed_on_stale_age() -> None:
    import main

    assert main._capture_reinit_needed(exc_streak=0, last_ok_age_s=0.6, stale_s=0.5) is True
    assert main._capture_reinit_needed(exc_streak=0, last_ok_age_s=0.4, stale_s=0.5) is False


def test_capture_reinit_needed_defensive_inputs() -> None:
    import main

    # Should not throw on weird inputs.
    assert isinstance(main._capture_reinit_needed(exc_streak=int("0"), last_ok_age_s=None), bool)
