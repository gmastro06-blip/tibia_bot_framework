from __future__ import annotations

from run_bot_ui import format_vital_text


def test_format_vital_text_prefers_absolute_then_pct() -> None:
    assert format_vital_text(100, 200, 50.0) == "100/200 (50.0%)"
    assert format_vital_text("100", "200", None) == "100/200 (50.0%)"


def test_format_vital_text_never_hides_partial_signals() -> None:
    # Current-only: must not show '?'
    assert format_vital_text(215, None, None) == "215"

    # Percent-only: must not show '?'
    assert format_vital_text(None, None, 42.0) == "42.0%"

    # Both missing: only case where '?' is allowed
    assert format_vital_text(None, None, None) == "?"
