from __future__ import annotations

from queue import Queue
from unittest.mock import Mock

from queue_health import put_latest_health


def test_put_latest_health_increments_drop_oldest() -> None:
    q: Queue[int] = Queue(maxsize=2)
    q.put_nowait(1)
    q.put_nowait(2)

    counters: dict[str, float] = {"drop_old": 0.0, "drop_new": 0.0}

    def inc(key: str, delta: float) -> None:
        counters[key] = float(counters.get(key, 0.0)) + float(delta)

    ok = put_latest_health(q, 3, inc=inc, drop_oldest_key="drop_old", drop_new_key="drop_new")
    assert ok is True
    assert counters["drop_old"] == 1.0
    assert counters["drop_new"] == 0.0


def test_put_latest_health_increments_drop_new_when_cannot_drop_oldest() -> None:
    q: Queue[int] = Queue(maxsize=1)
    q.put_nowait(1)

    # Force the internal "drop oldest" path to fail.
    q.get_nowait = Mock(side_effect=RuntimeError("cannot drop"))  # type: ignore[method-assign]

    counters: dict[str, float] = {"drop_old": 0.0, "drop_new": 0.0}

    def inc(key: str, delta: float) -> None:
        counters[key] = float(counters.get(key, 0.0)) + float(delta)

    ok = put_latest_health(q, 2, inc=inc, drop_oldest_key="drop_old", drop_new_key="drop_new")
    assert ok is False
    assert counters["drop_old"] == 0.0
    assert counters["drop_new"] == 1.0

    # Queue content unchanged.
    assert q.qsize() == 1
    assert list(getattr(q, "queue")) == [1]
