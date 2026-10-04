"""Request rate limits for the public authentication and invitation endpoints.

A sliding window per (bucket, key), kept in this process's memory. That is enough for one
server process; several processes or hosts would need a shared store (Redis or the
database) behind the same `limit()` call.
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from app.auth.errors import AuthError
from app.core.config import get_settings

# bucket -> (requests allowed, window in seconds)
LIMITS: dict[str, tuple[int, int]] = {
    "login": (10, 300),
    "signup": (5, 600),
    "verify": (20, 600),
    "resend": (6, 600),
    "invite_lookup": (30, 600),
    "invite_accept": (10, 600),
    "invite_create": (30, 3600),
}

_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def hit(bucket: str, key: str) -> None:
    """Counts one request; raises 429 once the bucket's limit for this key is reached."""
    if not get_settings().rate_limit_enabled:
        return
    allowed, window = LIMITS[bucket]
    now = time.monotonic()
    with _lock:
        stamps = _hits[(bucket, key)]
        while stamps and now - stamps[0] >= window:
            stamps.popleft()
        if len(stamps) >= allowed:
            retry = int(window - (now - stamps[0])) + 1
            raise AuthError(
                429,
                "rate_limited",
                "Too many attempts. Please wait a few minutes and try again.",
                headers={"Retry-After": str(retry)},
                retry_after=retry,
            )
        stamps.append(now)


def limit(request: Request, bucket: str, *keys: str | None) -> None:
    """Applies the bucket per client address and per each extra key (email, token hash)."""
    hit(bucket, f"ip:{client_ip(request)}")
    for key in keys:
        if key:
            hit(bucket, f"key:{key}")


def reset() -> None:
    with _lock:
        _hits.clear()
