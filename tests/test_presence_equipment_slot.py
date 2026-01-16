import numpy as np

from vision.presence import is_nonempty_equipment_slot


def _make_bgr(h: int, w: int, b: int, g: int, r: int) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :, 0] = np.uint8(b)
    img[:, :, 1] = np.uint8(g)
    img[:, :, 2] = np.uint8(r)
    return img


def test_equipment_slot_empty_with_border_is_false() -> None:
    # Empty slot can have a high-contrast border; inner area is uniform.
    img = _make_bgr(24, 24, 80, 80, 80)
    img[0, :, :] = 10
    img[-1, :, :] = 10
    img[:, 0, :] = 10
    img[:, -1, :] = 10

    assert is_nonempty_equipment_slot(img) is False


def test_equipment_slot_colored_icon_is_true() -> None:
    img = _make_bgr(24, 24, 80, 80, 80)
    # Put a small orange-ish patch in the interior.
    img[8:16, 8:16, :] = np.array([40, 140, 220], dtype=np.uint8)  # BGR

    assert is_nonempty_equipment_slot(img) is True


def test_equipment_slot_gray_texture_not_enough_is_false() -> None:
    rng = np.random.default_rng(0)
    base = 90
    noise = rng.normal(0.0, 10.0, size=(24, 24)).astype(np.float32)
    g = np.clip(base + noise, 0, 255).astype(np.uint8)
    img = np.stack([g, g, g], axis=2)

    assert is_nonempty_equipment_slot(img) is False


def test_equipment_slot_high_texture_grayscale_can_be_true() -> None:
    rng = np.random.default_rng(1)
    base = 110
    noise = rng.normal(0.0, 35.0, size=(24, 24)).astype(np.float32)
    g = np.clip(base + noise, 0, 255).astype(np.uint8)
    img = np.stack([g, g, g], axis=2)

    assert is_nonempty_equipment_slot(img, high_std=30.0) is True


def test_equipment_slot_high_std_but_no_dark_pixels_is_false() -> None:
    # Construct a crop with relatively high std but clamp away dark strokes.
    rng = np.random.default_rng(2)
    base = 150
    noise = rng.normal(0.0, 45.0, size=(24, 24)).astype(np.float32)
    g = np.clip(base + noise, 80, 230).astype(np.uint8)
    img = np.stack([g, g, g], axis=2)

    # With defaults, grayscale fallback requires some dark pixels (<55) in the interior.
    assert is_nonempty_equipment_slot(img, high_std=30.0) is False
