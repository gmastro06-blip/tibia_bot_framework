from __future__ import annotations

from vision import battlelist


def test_battlelist_stabilize_weighted_majority() -> None:
    buf: dict[int, list[battlelist.BattlelistEntry]] = {}

    # Feed a short history with some OCR noise. Stabilization should
    # converge to the weighted winner for row 0.
    frames = [
        {"row_index": 0, "name_raw": "Orc", "name_norm": "orc", "name_display": "Orc", "conf": 0.90},
        {"row_index": 0, "name_raw": "0rc", "name_norm": "orc", "name_display": "Orc", "conf": 0.80},
        {"row_index": 0, "name_raw": "Orc ", "name_norm": "orc", "name_display": "Orc", "conf": 0.85},
        {"row_index": 0, "name_raw": "Dragon", "name_norm": "dragon", "name_display": "Dragon", "conf": 0.95},
    ]

    out = []
    for e in frames:
        out = battlelist.stabilize(buf, [e], window_n=10)

    assert len(out) == 1
    stable = out[0]

    # Winner should be "orc" (sum 0.90+0.80+0.85 > 0.95).
    assert stable["name_norm"] == "orc"
    assert stable["name_display"] == "Orc"
    assert stable["name_raw"] == "Orc"

    # Stable confidence is winner_weight / total_weight.
    expected = (0.90 + 0.80 + 0.85) / (0.90 + 0.80 + 0.85 + 0.95)
    assert abs(float(stable["conf"]) - expected) < 1e-6
