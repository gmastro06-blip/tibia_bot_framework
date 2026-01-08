from __future__ import annotations

from decision.targeting import TargetSelector


def _box(cls: str, conf: float, x: float, y: float, w: float = 50, h: float = 50):
    return {
        "class": cls,
        "confidence": conf,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
    }


def test_targeting_picks_highest_score_near_center(monkeypatch):
    monkeypatch.delenv("TARGET_PRIORITY_CLASSES", raising=False)
    monkeypatch.setenv("TARGET_MIN_CONF", "0.0")
    monkeypatch.setenv("TARGET_CENTER_WEIGHT", "0.20")

    sel = TargetSelector()
    res = (1920, 1080)

    # Same confidence; one is closer to center.
    boxes = [
        _box("orc", 0.8, 100, 100),
        _box("orc", 0.8, 960, 540),
    ]

    t = sel.select_target(boxes, res)
    assert t is not None
    assert t.cls == "orc"
    assert (t.x, t.y) == (960, 540)


def test_targeting_respects_priority(monkeypatch):
    monkeypatch.setenv("TARGET_PRIORITY_CLASSES", "orc,dragon")
    monkeypatch.setenv("TARGET_MIN_CONF", "0.0")

    sel = TargetSelector()
    res = (1920, 1080)

    boxes = [
        _box("dragon", 0.99, 960, 540),
        _box("orc", 0.30, 960, 540),
    ]

    # Even though dragon has higher confidence, priority picks orc.
    t = sel.select_target(boxes, res)
    assert t is not None
    assert t.cls == "orc"


def test_targeting_is_sticky_when_delta_small(monkeypatch):
    monkeypatch.delenv("TARGET_PRIORITY_CLASSES", raising=False)
    monkeypatch.setenv("TARGET_MIN_CONF", "0.0")
    monkeypatch.setenv("TARGET_CENTER_WEIGHT", "0.0")
    monkeypatch.setenv("TARGET_SWITCH_DELTA", "0.10")
    monkeypatch.setenv("TARGET_MATCH_MAX_DIST_NORM", "1.0")

    sel = TargetSelector()
    res = (1920, 1080)

    prev = sel.select_target([_box("orc", 0.70, 500, 500)], res)
    assert prev is not None

    # New best is only slightly higher; should keep prev due to switch delta.
    boxes2 = [_box("orc", 0.71, 505, 505), _box("dragon", 0.72, 900, 500)]
    t2 = sel.select_target(boxes2, res, prev=prev)
    assert t2 is not None
    assert t2.cls == "orc"
