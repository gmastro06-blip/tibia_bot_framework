from queue import Full, Queue

from queue_utils import put_latest


def test_put_latest_drops_oldest_and_keeps_newest() -> None:
    q: Queue[int] = Queue(maxsize=1)

    assert put_latest(q, 1) is True
    assert q.get_nowait() == 1

    # Fill again and then push a new value; newest should win.
    assert put_latest(q, 1) is True

    dropped_oldest = 0
    dropped_new = 0

    def on_oldest() -> None:
        nonlocal dropped_oldest
        dropped_oldest += 1

    def on_new() -> None:
        nonlocal dropped_new
        dropped_new += 1

    assert put_latest(q, 2, on_drop_oldest=on_oldest, on_drop_new=on_new) is True
    assert dropped_oldest == 1
    assert dropped_new == 0

    assert q.get_nowait() == 2


def test_put_latest_can_drop_new_item_when_racy() -> None:
    class _FakeRacyQueue:
        def __init__(self) -> None:
            self._puts = 0

        def put_nowait(self, _item: int) -> None:
            # Always full (both initial attempt and retry)
            self._puts += 1
            raise Full()

        def get_nowait(self) -> int:
            # Dropping the oldest succeeds
            return 123

    dropped_oldest = 0
    dropped_new = 0

    def on_oldest() -> None:
        nonlocal dropped_oldest
        dropped_oldest += 1

    def on_new() -> None:
        nonlocal dropped_new
        dropped_new += 1

    out = put_latest(_FakeRacyQueue(), 2, on_drop_oldest=on_oldest, on_drop_new=on_new)
    assert out is False
    assert dropped_oldest == 1
    assert dropped_new == 1
