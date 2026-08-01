"""Circuit breakers, TTL caching, and rate limiting.

The audit found the prototype's provider loops were *fallbacks, not retries*:
no backoff, no jitter, no rate limiting, and a fresh ``httpx.Client`` per
request at six call sites. A transient 429 from Google News permanently lost
that provider for the request.

It also found an untethered ``@lru_cache`` on page fetches with no TTL, so a
page read at 09:00 was served at 21:00 underneath a header asserting the current
UTC time — directly defeating the product's headline feature.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class CircuitOpenError(Exception):
    """The breaker is open; the call was not attempted."""


@dataclass(slots=True)
class CircuitBreaker:
    """Trips after repeated failures, then probes with a single request."""

    name: str
    failure_threshold: int = 3
    reset_after_s: float = 60.0

    _failures: int = 0
    _opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.reset_after_s:
            # Half-open: allow one probe through.
            self._opened_at = None
            self._failures = self.failure_threshold - 1
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold and self._opened_at is None:
            self._opened_at = time.monotonic()
            log.warning("circuit.open", provider=self.name, failures=self._failures)

    def check(self) -> None:
        if self.is_open:
            raise CircuitOpenError(f"{self.name} is temporarily unavailable")


@dataclass(slots=True)
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    """A small bounded cache with a real expiry.

    Unlike ``lru_cache``, an entry goes stale. News results are cached for
    minutes, not for the lifetime of the process.
    """

    def __init__(self, *, ttl_s: float = 300.0, max_entries: int = 256) -> None:
        self._ttl = ttl_s
        self._max = max_entries
        self._data: dict[str, _Entry] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            self.misses += 1
            return None
        if time.monotonic() >= entry.expires_at:
            del self._data[key]
            self.misses += 1
            return None
        self.hits += 1
        return entry.value

    def set(self, key: str, value: Any, *, ttl_s: float | None = None) -> None:
        if len(self._data) >= self._max:
            # Evict the entry closest to expiry.
            oldest = min(self._data, key=lambda k: self._data[k].expires_at)
            del self._data[oldest]
        self._data[key] = _Entry(value, time.monotonic() + (ttl_s or self._ttl))

    def clear(self) -> None:
        self._data.clear()


@dataclass(slots=True)
class RateLimiter:
    """Minimum spacing between calls to one provider.

    GDELT in particular rate-limits aggressively; the brief calls it out by name.
    """

    min_interval_s: float
    _last: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self) -> None:
        async with self._lock:
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


async def with_backoff[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay_s: float = 0.4,
    max_delay_s: float = 4.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Retry with exponential backoff and full jitter.

    Jitter matters: without it, several providers failing at once retry in
    lockstep and re-create the burst that tripped the limit.
    """
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except retry_on as exc:
            last = exc
            if attempt == attempts - 1:
                break
            delay = min(max_delay_s, base_delay_s * (2**attempt))
            await asyncio.sleep(random.uniform(0, delay))  # noqa: S311 - not crypto
    assert last is not None
    raise last
