# src/utils/concurrency.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Deque, Generic, Optional, TypeVar
from collections import deque
import threading

T = TypeVar("T")


class LatestQueue(Generic[T]):
    """
    Cola tipo 'latest only' con capacidad limitada.
    - put(x, drop_oldest=True): mete y opcionalmente descarta el más viejo si está llena
    - get_latest(): devuelve el último elemento disponible (o None)
    - thread-safe
    """

    def __init__(self, max_size: int = 1):
        if max_size < 1:
            raise ValueError("max_size debe ser >= 1")
        self.max_size = max_size
        self._buf: Deque[T] = deque(maxlen=max_size)
        self._lock = threading.Lock()

    def put(self, item: T, drop_oldest: bool = False) -> None:
        with self._lock:
            if len(self._buf) >= self.max_size and drop_oldest:
                # deque(maxlen=...) ya descarta automáticamente al hacer append,
                # pero lo dejamos explícito por claridad
                # (si maxlen está activo, append() descarta el más viejo)
                pass
            self._buf.append(item)

    def get_latest(self) -> Optional[T]:
        with self._lock:
            if not self._buf:
                return None
            return self._buf[-1]

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)


# Alias explícitos para tu arquitectura
class FrameQueue(LatestQueue):
    pass


class GameStateQueue(LatestQueue):
    pass
