"""One-time codes.

Two separate concerns:

  OTPService   the rules: generate, store only a hash, expire, limit attempts, single use.
  OtpSender    delivery. Email is the only channel today. `EmailOtpSender` sends by SMTP;
               `LocalDemoOtpSender` is the development fallback when no mail server is
               configured, and it says so. A later SMS or authenticator-app sender would
               implement the same two-line protocol.
"""

import hashlib
import hmac
import logging
import secrets
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.errors import AuthError
from app.core.config import Settings, get_settings
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
        message = EmailMessage()
        message["Subject"] = f"{code} is your Next Best Action verification code"
        message["From"] = self.settings.smtp_from or self.settings.smtp_user
        message["To"] = user.email
        message.set_content(
            f"Hello {user.display_name},\n\n"
            f"Your verification code is {code}.\n"
            f"It expires in {minutes} minutes and can be used once.\n\n"
            "If you did not create an account, you can ignore this message.\n"
        )
        return message

    def send(self, user: User, code: str, minutes: int) -> None:
        s = self.settings
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as server:
            server.starttls()
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password or "")
            server.send_message(self.build(user, code, minutes))


class LocalDemoOtpSender:
    """Development only: nothing is sent. The code is logged and shown on the verify screen."""

    channel = DEVELOPMENT

    def send(self, user: User, code: str, minutes: int) -> None:
        log.warning("DEVELOPMENT one-time code for %s: %s (no email sent)", user.email, code)


def get_sender(settings: Settings | None = None) -> OtpSender:
    settings = settings or get_settings()
    if settings.smtp_host:
        return EmailOtpSender(settings)
    if settings.demo_mode:
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
    key = get_settings().jwt_secret.encode()
    return hmac.new(key, f"{user.id}:{code}".encode(), hashlib.sha256).hexdigest()


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
            log.error("Could not email one-time code to %s: %s", user.email, type(exc).__name__)
            if not settings.demo_mode:
                raise AuthError(
                    502, "otp_delivery_failed", "The verification email could not be sent."
                ) from exc
            sender = LocalDemoOtpSender()
            sender.send(user, code, settings.otp_ttl_minutes)

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
