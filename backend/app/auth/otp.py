"""One-time codes.

Two separate concerns:

  OTPService   the rules: generate, store only a hash, expire, limit attempts, single use.
  OtpSender    delivery. Email is the only channel today. `EmailOtpSender` sends by SMTP;
               `LocalDemoOtpSender` is the development fallback when no mail server is
               configured, and it says so. A later SMS or authenticator-app sender would
               implement the same two-line protocol.

Once a mail server is configured, a failed send is reported as a failure
(`otp_delivery_failed`) and no code is stored; the code is never shown on screen instead,
because that would let someone verify an address they do not control. Only the exception
type is logged, never the server's reply, the address book or any credential.
"""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.errors import AuthError
from app.core.config import Settings, get_settings
from app.core.security import derived_key
from app.mail.templates import otp_email
from app.mail.transport import SmtpTransport
from app.models import OtpChallenge, User
from app.models.tables import utcnow

log = logging.getLogger("nba.otp")

EMAIL, DEVELOPMENT = "email", "development"


class OtpSender(Protocol):
    channel: str

    def send(self, user: User, code: str, minutes: int) -> None:
        """Deliver the code. Raise on failure."""


class EmailOtpSender:
    channel = EMAIL

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build(self, user: User, code: str, minutes: int) -> EmailMessage:
        return otp_email(user.email, user.display_name, code, minutes)

    def send(self, user: User, code: str, minutes: int) -> None:
        SmtpTransport(self.settings).deliver(self.build(user, code, minutes))


class LocalDemoOtpSender:
    """Local development only: nothing is sent and the code is shown on the verify screen.
    The code itself is never written to the log."""

    channel = DEVELOPMENT

    def send(self, user: User, code: str, minutes: int) -> None:
        log.warning("Development one-time code issued for user %s (no email sent)", user.id)


def get_sender(settings: Settings | None = None) -> OtpSender:
    settings = settings or get_settings()
    if settings.smtp_host:
        return EmailOtpSender(settings)
    if settings.show_development_codes:
        return LocalDemoOtpSender()
    raise AuthError(503, "otp_delivery_unavailable", "Email delivery is not configured.")


@dataclass
class Issued:
    delivery: str
    expires_in: int
    resend_in: int
    # Present only for the development sender. Never set when a real email was sent.
    dev_code: str | None = None


def _hash(user: User, code: str) -> str:
    return hmac.new(derived_key("otp"), f"{user.id}:{code}".encode(), hashlib.sha256).hexdigest()


class OTPService:
    def __init__(self, now=utcnow) -> None:
        self._now = now

    def _current(self, db: Session, user: User) -> OtpChallenge | None:
        return db.scalar(
            select(OtpChallenge)
            .where(OtpChallenge.user_id == user.id)
            .order_by(OtpChallenge.id.desc())
        )

    def _clear(self, db: Session, user: User) -> None:
        db.execute(delete(OtpChallenge).where(OtpChallenge.user_id == user.id))

    def issue(self, db: Session, user: User, *, respect_cooldown: bool = True) -> Issued:
        """Replaces any earlier code for this account with a new one and delivers it."""
        settings = get_settings()
        now = self._now()
        existing = self._current(db, user)
        if respect_cooldown and existing and now < existing.resend_available_at:
            wait = int((existing.resend_available_at - now).total_seconds()) + 1
            raise AuthError(
                429, "otp_resend_wait", f"Wait {wait} seconds before requesting another code."
            )
        code = f"{secrets.randbelow(10**6):06d}"
        sender = get_sender(settings)
        try:
            sender.send(user, code, settings.otp_ttl_minutes)
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - any delivery failure is handled the same way
            log.error("Could not email one-time code to user %s: %s", user.id, type(exc).__name__)
            # Any earlier code is withdrawn too, so the account has no usable code at all.
            self._clear(db, user)
            db.flush()
            raise AuthError(
                502,
                "otp_delivery_failed",
                "We couldn't send the verification email. Check the address and try again.",
            ) from exc

        self._clear(db, user)
        db.add(
            OtpChallenge(
                user_id=user.id,
                code_hash=_hash(user, code),
                expires_at=now + timedelta(minutes=settings.otp_ttl_minutes),
                resend_available_at=now + timedelta(seconds=settings.otp_resend_seconds),
                created_at=now,
            )
        )
        db.flush()
        return Issued(
            delivery=sender.channel,
            expires_in=settings.otp_ttl_minutes * 60,
            resend_in=settings.otp_resend_seconds,
            dev_code=code if sender.channel == DEVELOPMENT else None,
        )

    def verify(self, db: Session, user: User, code: str) -> None:
        """Returns on success, having deleted the code. Raises AuthError otherwise."""
        settings = get_settings()
        challenge = self._current(db, user)
        if challenge is None:
            raise AuthError(400, "otp_missing", "No active code. Request a new one.")
        if self._now() >= challenge.expires_at:
            self._clear(db, user)
            raise AuthError(400, "otp_expired", "This code has expired. Request a new one.")
        if challenge.attempts >= settings.otp_max_attempts:
            self._clear(db, user)
            raise AuthError(429, "otp_locked", "Too many attempts. Request a new code.")
        if not hmac.compare_digest(challenge.code_hash, _hash(user, code.strip())):
            challenge.attempts += 1
            left = settings.otp_max_attempts - challenge.attempts
            if left <= 0:
                self._clear(db, user)
                raise AuthError(429, "otp_locked", "Too many attempts. Request a new code.")
            raise AuthError(400, "otp_incorrect", f"Incorrect code. {left} attempts left.")
        self._clear(db, user)

    def status(self, db: Session, user: User) -> dict:
        """Seconds until expiry and until a resend is allowed (0 if no active code)."""
        challenge = self._current(db, user)
        if challenge is None:
            return {"expires_in": 0, "resend_in": 0}
        now = self._now()
        return {
            "expires_in": max(0, int((challenge.expires_at - now).total_seconds())),
            "resend_in": max(0, int((challenge.resend_available_at - now).total_seconds())),
        }

    def purge(self, db: Session, now: datetime | None = None) -> None:
        """Drops expired codes so nothing lingers after it stops being usable."""
        db.execute(delete(OtpChallenge).where(OtpChallenge.expires_at <= (now or self._now())))


otp_service = OTPService()
