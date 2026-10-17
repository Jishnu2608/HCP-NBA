"""Opaque references for telemetry and diagnostics: a keyed hash in place of a record id.

The key comes from the deployment's own secret when one is set (`NBA_JWT_SECRET`), so the same
record maps to the same reference across processes and counts of distinct subjects stay
stable; otherwise from a random key held only by this process. A reference cannot be turned
back into an id without the key, and the key is never logged or returned.
"""

import hashlib
import hmac
import secrets
from functools import lru_cache

from app.core.config import get_settings


@lru_cache(maxsize=1)
def _key() -> bytes:
    secret = get_settings().jwt_secret
    base = secret.encode() if secret else secrets.token_bytes(32)
    return hashlib.sha256(b"nba-pseudonym|" + base).digest()


def ref(kind: str, value: str | int | None) -> str | None:
    """16 hex characters for (kind, value); None for None."""
    if value is None:
        return None
    return hmac.new(_key(), f"{kind}|{value}".encode(), hashlib.sha256).hexdigest()[:16]
