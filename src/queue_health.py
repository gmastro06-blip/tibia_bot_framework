from __future__ import annotations

from queue import Queue
from typing import Callable, TypeVar

from queue_utils import put_latest

T = TypeVar("T")


def put_latest_health(
    q: Queue[T],
    item: T,
    *,
    inc: Callable[[str, float], None],
    drop_oldest_key: str | None = None,
    drop_new_key: str | None = None,
) -> bool:
    """Put with latest-wins semantics and update health counters.

    This is a small, dependency-free helper used by the threaded pipeline.

    Returns True when enqueued, False when the new item was dropped.
    """

    def on_oldest() -> None:
        if drop_oldest_key:
            try:
                inc(drop_oldest_key, 1.0)
            except Exception:
                pass

    def on_new() -> None:
        if drop_new_key:
            try:
                inc(drop_new_key, 1.0)
            except Exception:
                pass

    try:
        return bool(put_latest(q, item, on_drop_oldest=on_oldest, on_drop_new=on_new))
    except Exception:
        # Fail-safe: if anything goes wrong, count as new-drop.
        on_new()
        return False
