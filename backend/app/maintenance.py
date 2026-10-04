"""Deletes security records that have stopped being useful: expired or idle sessions,
expired one-time codes and old rate-limit counters. Run at start-up and after each sign-in
(cheap: indexed deletes). Nothing with an undefined retention period (accounts, audit log,
consent records, invitations, privacy requests) is touched here.
"""

from datetime import timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.config import get_settings
from app.models import OtpChallenge, RateLimitHit, UserSession
from app.models.tables import utcnow


def purge_expired(db: Session) -> dict[str, int]:
    now = utcnow()
    idle = timedelta(minutes=get_settings().session_idle_minutes)
    counts = {
        "sessions": db.execute(
            delete(UserSession).where(
                or_(UserSession.expires_at <= now, UserSession.last_seen_at <= now - idle)
            )
        ).rowcount,
        "codes": db.execute(delete(OtpChallenge).where(OtpChallenge.expires_at <= now)).rowcount,
        "rate_limit": db.execute(
            delete(RateLimitHit).where(
                RateLimitHit.ts <= now - timedelta(seconds=ratelimit.LONGEST)
            )
        ).rowcount,
    }
    return counts
