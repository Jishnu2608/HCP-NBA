"""The single path by which the application sends email.

`SmtpTransport` uses the NBA_SMTP_* settings (STARTTLS). Without a mail server there is no
transport (`get_mailer` returns None) and callers fall back to their development behaviour,
which always says plainly that nothing was sent.

Failures raise; callers log only the exception type, never the server's reply or any
credential.
"""

import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.core.config import Settings, get_settings


class Mailer(Protocol):
    def deliver(self, message: EmailMessage) -> None:
        """Send the message. Raise on failure."""


class SmtpTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def sender(self) -> str:
        return self.settings.smtp_from or self.settings.smtp_user or ""

    def deliver(self, message: EmailMessage) -> None:
        s = self.settings
        if "From" not in message:
            message["From"] = self.sender
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as server:
            server.starttls()
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password or "")
            server.send_message(message)


def get_mailer(settings: Settings | None = None) -> Mailer | None:
    settings = settings or get_settings()
    return SmtpTransport(settings) if settings.smtp_host else None


def support_contact(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return settings.support_email or settings.smtp_from or settings.smtp_user or ""
