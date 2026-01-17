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


def test_is_hungry_hsv_detects_orange_blob() -> None:
    # Synthetic positive: a centered orange/yellow blob should be detected.
    # If OpenCV isn't installed in this environment, skip (function returns False).
    try:
        import cv2  # noqa: F401
    except Exception as e:
        import pytest

        pytest.skip(f"cv2 no disponible: {e}")

    from src.vision.presence import is_hungry_hsv

    img = np.zeros((40, 40, 3), dtype=np.uint8)
    # BGR orange (common): should land in the default HSV band.
    img[10:30, 10:30] = (0, 165, 255)

    assert is_hungry_hsv(img) is True

    # Synthetic negative: pure blue should not match the orange/yellow band.
    img2 = np.zeros((40, 40, 3), dtype=np.uint8)
    img2[10:30, 10:30] = (255, 0, 0)
    assert is_hungry_hsv(img2) is False
