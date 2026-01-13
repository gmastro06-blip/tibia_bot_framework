from decision.recovery import choose_sidestep_direction


def test_choose_sidestep_direction_for_vertical_intent() -> None:
    assert choose_sidestep_direction("north", attempt=0) == "east"
    assert choose_sidestep_direction("north", attempt=1) == "west"
    assert choose_sidestep_direction("south", attempt=2) == "east"


def test_choose_sidestep_direction_for_horizontal_intent() -> None:
    assert choose_sidestep_direction("east", attempt=0) == "north"
    assert choose_sidestep_direction("east", attempt=1) == "south"
    assert choose_sidestep_direction("west", attempt=2) == "north"


def test_choose_sidestep_direction_rejects_unknown() -> None:
    assert choose_sidestep_direction("", attempt=0) is None
    assert choose_sidestep_direction("foo", attempt=0) is None
