"""The RateLimiter adapter: a sliding window of hit times kept in process memory.

Counts live in this process only, so the limits hold for one uvicorn worker
(docs/deploy.md runs exactly one) and reset on restart.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from questly.core.auth.service import RateLimitedError


@dataclass(frozen=True, slots=True)
class RateLimit:
    """At most `limit` requests per key in any `window_seconds`."""

    limit: int
    window_seconds: float


class SlidingWindowRateLimiter:
    """Keep each key's accepted hits within the last window, under one lock.

    A refused request is not recorded, so a key never holds more than its
    bucket's `limit` timestamps. Keys whose hits have all expired are dropped
    once per longest window, which keeps memory bounded by recent traffic.
    """

    def __init__(
        self,
        limits: Mapping[str, RateLimit],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = dict(limits)
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._sweep_every = max(rule.window_seconds for rule in self._limits.values())
        self._next_sweep = clock() + self._sweep_every

    def hit(self, bucket: str, key: str) -> None:
        rule = self._limits[bucket]
        with self._lock:
            now = self._clock()
            if now >= self._next_sweep:
                self._sweep(now)
            hits = self._hits.setdefault((bucket, key), deque())
            _drop_expired(hits, now - rule.window_seconds)
            if len(hits) >= rule.limit:
                retry_after = math.ceil(hits[0] + rule.window_seconds - now)
                raise RateLimitedError(max(1, retry_after))
            hits.append(now)

    def tracked_keys(self) -> int:
        """How many (bucket, key) pairs the limiter is holding hits for."""
        with self._lock:
            return len(self._hits)

    def _sweep(self, now: float) -> None:
        for (bucket, key), hits in list(self._hits.items()):
            _drop_expired(hits, now - self._limits[bucket].window_seconds)
            if not hits:
                del self._hits[(bucket, key)]
        self._next_sweep = now + self._sweep_every


def _drop_expired(hits: deque[float], cutoff: float) -> None:
    while hits and hits[0] <= cutoff:
        hits.popleft()
