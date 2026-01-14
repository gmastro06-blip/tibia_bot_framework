from __future__ import annotations

from queue import Queue

from queue_utils import put_latest


def test_put_latest_drops_oldest_and_keeps_latest_items() -> None:
    q: Queue[int] = Queue(maxsize=2)

    dropped_oldest = 0
    dropped_new = 0

    def on_oldest() -> None:
        nonlocal dropped_oldest
        dropped_oldest += 1

    def on_new() -> None:
        nonlocal dropped_new
        dropped_new += 1

    assert put_latest(q, 1, on_drop_oldest=on_oldest, on_drop_new=on_new) is True
    assert put_latest(q, 2, on_drop_oldest=on_oldest, on_drop_new=on_new) is True

    # Queue full: enqueueing 3 should drop oldest (1) and keep (2,3)
    assert put_latest(q, 3, on_drop_oldest=on_oldest, on_drop_new=on_new) is True

    assert dropped_oldest == 1
    assert dropped_new == 0
    assert q.qsize() == 2

    a = q.get_nowait()
    b = q.get_nowait()
    assert (a, b) == (2, 3)
