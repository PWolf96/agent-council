"""Background execution seam.

``InProcessExecutor`` runs each (blocking) workflow on a bounded thread pool, so
many runs proceed concurrently without starving the event loop. A future
``ArqExecutor``/``CeleryExecutor`` can implement the same protocol to move work
onto a durable queue without touching the RunManager.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Protocol


class RunExecutor(Protocol):
    def submit(self, run_id: str, fn: Callable[[], None]) -> None:
        ...

    def shutdown(self) -> None:
        ...


class InProcessExecutor:
    def __init__(self, max_workers: int = 4):
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="run"
        )

    def submit(self, run_id: str, fn: Callable[[], None]) -> None:
        self._pool.submit(fn)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
