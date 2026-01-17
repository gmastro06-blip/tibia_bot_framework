from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "cur,mx,last_cur,last_mx,exp_cur,exp_mx,exp_reason",
    [
        # Already sane.
        (50, 100, None, None, 50, 100, ""),
        # Invalid max.
        (10, 0, None, None, None, None, "invalid_max"),
        # Invalid current.
        (-1, 100, None, None, None, 100, "invalid_cur"),
        # cur > max -> reuse last when last max matches.
        (150, 100, 80, 100, 80, 100, "cur_gt_max_use_last"),
        # cur > max -> drop cur when no compatible last.
        (150, 100, 80, 200, None, 100, "cur_gt_max_drop_cur"),
        # Non-int current -> invalid.
        ("oops", 100, None, None, None, 100, "invalid_int"),
    ],
)
def test_sanitize_current_max(cur, mx, last_cur, last_mx, exp_cur, exp_mx, exp_reason) -> None:
    from gamestate.builder import _sanitize_current_max

    out_cur, out_mx, reason = _sanitize_current_max(cur, mx, last_cur=last_cur, last_mx=last_mx)
    assert out_cur == exp_cur
    assert out_mx == exp_mx
    assert reason == exp_reason
