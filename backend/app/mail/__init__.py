"""Outgoing email: one SMTP transport and the message templates that use it."""

from app.mail.transport import Mailer, SmtpTransport, get_mailer

__all__ = ["Mailer", "SmtpTransport", "get_mailer"]
