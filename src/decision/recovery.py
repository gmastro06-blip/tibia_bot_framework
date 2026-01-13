from __future__ import annotations


def choose_sidestep_direction(intended: str, *, attempt: int) -> str | None:
    """Pick a perpendicular move direction to try escaping a blockage.

    This is *assistant-only* logic (no input injection). It returns a direction
    string compatible with our ActionRequest planner.

    Args:
        intended: The original intended move direction (north/south/east/west).
        attempt: Zero-based attempt counter.

    Returns:
        A perpendicular direction (alternating each attempt) or None.
    """

    d = str(intended or "").strip().lower()
    if d not in {"north", "south", "east", "west"}:
        return None

    try:
        a = int(attempt)
    except Exception:
        a = 0

    # Alternate perpendicular directions each attempt.
    if d in {"north", "south"}:
        return "east" if (a % 2 == 0) else "west"
    return "north" if (a % 2 == 0) else "south"
