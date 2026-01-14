from __future__ import annotations

from queue import Empty, Full, Queue
from typing import Callable, Optional, TypeVar


T = TypeVar("T")


def put_latest(
    q: Queue[T],
    item: T,
    *,
    on_drop_oldest: Optional[Callable[[], None]] = None,
    on_drop_new: Optional[Callable[[], None]] = None,
) -> bool:
    """Put an item into a bounded queue, ensuring *latest-wins* semantics.

    If the queue is full, this drops exactly one oldest item and retries once.
    If it still cannot enqueue (e.g. multiple producers racing), it drops the
    new item.

    Returns True when the item was enqueued, False when the new item was dropped.
    """

    try:
        q.put_nowait(item)
        return True
    except Full:
        pass
    except Exception:
        # Unknown queue failure: fail safe by dropping the new item.
        try:
            if on_drop_new:
                on_drop_new()
        finally:
            return False

    # Queue full: drop one oldest item.
    try:
        q.get_nowait()
        if on_drop_oldest:
            on_drop_oldest()
    except Empty:
        # Race: became empty; continue to put.
        pass
    except Exception:
        # If we cannot drop, we cannot guarantee latest-wins.
        try:
            if on_drop_new:
                on_drop_new()
        finally:
            return False

    # Retry put once.
    try:
        q.put_nowait(item)
        return True
    except Full:
        # Still full (race): drop new item.
        try:
            if on_drop_new:
                on_drop_new()
        finally:
            return False
    except Exception:
        try:
            if on_drop_new:
                on_drop_new()
        finally:
            return False
