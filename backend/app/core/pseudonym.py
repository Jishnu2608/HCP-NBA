"""Opaque references for telemetry and diagnostics: a keyed hash in place of a record id.

The key is derived from the deployment's own secret (`NBA_JWT_SECRET`), so the same record maps
to the same reference in every process (web server and worker) and counts of distinct subjects
stay stable. There is no fallback key: without the secret `ref` raises `PseudonymUnavailable`
(naming the variable, never a value), so AI scope subjects can never silently depend on a key
that differs per process, and no raw identifier is ever used in place of a reference. The web
server and the worker already refuse to start without the secret. A reference cannot be turned
back into an id without the key, and the key is never logged or returned.
"""

import hashlib
import hmac
from functools import lru_cache

from app.core.config import MissingSecret, get_settings


class PseudonymUnavailable(MissingSecret):
    """No stable pseudonym key: the deployment secret is not configured."""


@lru_cache(maxsize=4)
def _key_for(secret: str) -> bytes:
    return hashlib.sha256(b"nba-pseudonym|" + secret.encode()).digest()


def _key() -> bytes:
    secret = get_settings().jwt_secret
    if not secret:
        raise PseudonymUnavailable("NBA_JWT_SECRET")
    return _key_for(secret)


def available() -> bool:
    """True when references can be made (the deployment secret is set)."""
    return bool(get_settings().jwt_secret)


def ref(kind: str, value: str | int | None) -> str | None:
    """16 hex characters for (kind, value); None for None. Raises `PseudonymUnavailable` when
    the deployment secret is missing."""
    if value is None:
        return None
    return hmac.new(_key(), f"{kind}|{value}".encode(), hashlib.sha256).hexdigest()[:16]
