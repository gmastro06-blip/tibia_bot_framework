from __future__ import annotations

from input_bridge import is_allowed_key


def test_whitelist_keys() -> None:
    for k in ["W", "A", "S", "D"]:
        assert is_allowed_key(k)

    for i in range(1, 13):
        assert is_allowed_key(f"F{i}")

    for k in ["PGDN", "PAGEUP", "HOME", "END", "INSERT", "DELETE"]:
        assert is_allowed_key(k)
