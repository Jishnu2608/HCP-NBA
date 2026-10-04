"""Rate limits for sign-in, sign-up, one-time codes and invitations.

Each counted attempt is a row in `rate_limit_hit` (bucket, keyed hash of the key, time),
so limits survive a restart and are shared by every server process using the database.
A limit is a sliding window: an attempt is refused while `limit` attempts already fall
inside the last `window` seconds, and the answer carries `Retry-After`.

Keys are chosen so that knowing someone's email is not enough to lock them out:

  sign-in failures   per email + client address (5 / 15 min) and per address (20 / 15 min);
                     only failures count, and a successful sign-in clears its email + address
                     counter. An attacker on another address cannot block the owner.
  sign-up            per address (5 / 15 min)
  code emails        per email + address (3 / 10 min), counted only when a code (or the
                     equivalent notice) is actually sent; the 30-second resend cool-down
                     still applies on top
  code entry         per pending account (5 / 10 min), so a fresh code does not reset it
  invitation accept  per address (5 / 15 min); lookups per address (30 / 10 min); invitations
                     sent per inviter (30 / hour)

The keys themselves (addresses, emails) are never stored, only a keyed hash.
"""

from datetime import timedelta

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.errors import AuthError
from app.core.config import get_settings
from app.core.security import fingerprint
from app.models import RateLimitHit
from app.models.tables import utcnow

MINUTE = 60
# bucket -> (attempts allowed, window in seconds)
LIMITS: dict[str, tuple[int, int]] = {
    "login_fail_account": (5, 15 * MINUTE),
    "login_fail_ip": (20, 15 * MINUTE),
    "signup_ip": (5, 15 * MINUTE),
    "otp_send": (3, 10 * MINUTE),
    "otp_verify": (5, 10 * MINUTE),
    "invite_accept_ip": (5, 15 * MINUTE),
    "invite_lookup_ip": (30, 10 * MINUTE),
    "invite_create": (30, 60 * MINUTE),
}
LONGEST = max(window for _, window in LIMITS.values())

MESSAGE = "Too many attempts. Please try again later."


def _now():
    return utcnow()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _hash(bucket: str, key: str) -> str:
    return fingerprint(f"{bucket}|{key}")


def _enabled() -> bool:
    return get_settings().rate_limit_enabled


def check(db: Session, bucket: str, key: str) -> None:
    """Raises 429 if the limit for this key is already used up. Records nothing."""
    if not _enabled():
        return
    allowed, window = LIMITS[bucket]
    now = _now()
    since = now - timedelta(seconds=window)
    count, oldest = db.execute(
        select(func.count(), func.min(RateLimitHit.ts)).where(
            RateLimitHit.bucket == bucket,
            RateLimitHit.key_hash == _hash(bucket, key),
            RateLimitHit.ts > since,
        )
    ).one()
    if count >= allowed:
        retry = max(1, int((oldest + timedelta(seconds=window) - now).total_seconds()) + 1)
        # Generic on purpose: nothing about which limit, which key, or whether an account
        # exists.
        raise AuthError(429, "rate_limited", MESSAGE, headers={"Retry-After": str(retry)})


def record(db: Session, bucket: str, key: str) -> None:
    """Counts one attempt. The caller's transaction commits it."""
    if not _enabled():
        return
    now = _now()
    db.execute(delete(RateLimitHit).where(RateLimitHit.ts <= now - timedelta(seconds=LONGEST)))
    db.add(RateLimitHit(bucket=bucket, key_hash=_hash(bucket, key), ts=now))
    db.flush()


def consume(db: Session, bucket: str, key: str) -> None:
    """Check, then count this attempt."""
    check(db, bucket, key)
    record(db, bucket, key)


def clear(db: Session, bucket: str, key: str) -> None:
    """Forget the attempts for this key (a successful sign-in clears its failures)."""
    if not _enabled():
        return
    db.execute(
        delete(RateLimitHit).where(
            RateLimitHit.bucket == bucket, RateLimitHit.key_hash == _hash(bucket, key)
        )
    )


def reset(db: Session) -> None:
    db.execute(delete(RateLimitHit))
