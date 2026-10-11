"""Token generation, constant-time comparison and failure rate limiting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque


def new_token(nbytes: int = 32) -> str:
    """Return a URL-safe token with ``nbytes`` of CSPRNG entropy."""
    return base64.urlsafe_b64encode(secrets.token_bytes(nbytes)).rstrip(b"=").decode()


def new_id() -> str:
    return new_token(12)


def token_digest(token: str) -> str:
    """SHA-256 digest used so raw bearer tokens are never kept in memory maps."""
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class FailureLimiter:
    """Sliding-window limiter on *failed* attempts, bounded in memory."""

    def __init__(self, limit: int, window: float, max_keys: int = 256, clock=time.monotonic):
        self._limit = limit
        self._window = window
        self._max_keys = max_keys
        self._clock = clock
        self._fails: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        dq = self._fails.setdefault(key, deque())
        while dq and now - dq[0] > self._window:
            dq.popleft()
        return dq

    def blocked(self, key: str) -> bool:
        with self._lock:
            now = self._clock()
            if key not in self._fails:
                return False
            dq = self._prune(key, now)
            if not dq:
                del self._fails[key]
                return False
            return len(dq) >= self._limit

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            if key not in self._fails and len(self._fails) >= self._max_keys:
                # Evict the oldest key to keep memory bounded under spraying.
                oldest = min(self._fails, key=lambda k: self._fails[k][-1] if self._fails[k] else 0)
                del self._fails[oldest]
            self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
