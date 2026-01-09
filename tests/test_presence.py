import numpy as np


def test_is_nonempty_icon_empty_black() -> None:
    from src.vision.presence import is_nonempty_icon

    img = np.zeros((20, 20, 3), dtype=np.uint8)
    assert is_nonempty_icon(img) is False


def test_is_nonempty_icon_nonempty_pattern() -> None:
    from src.vision.presence import is_nonempty_icon

    img = np.zeros((30, 30, 3), dtype=np.uint8)
    img[10:20, 10:20] = 255
    assert is_nonempty_icon(img) is True


def test_is_hungry_hsv_runs_without_cv2() -> None:
    # Ensure function exists and returns a boolean even when cv2 isn't available.
    from src.vision.presence import is_hungry_hsv

    img = np.zeros((10, 10, 3), dtype=np.uint8)
    out = is_hungry_hsv(img)
    assert out in (True, False)
