"""Email templates. Every message has a plain-text part and, where it helps, an HTML part.

The HTML is deliberately old-fashioned so it renders the same in Gmail, Outlook and Apple
Mail: table layout, inline styles, system fonts, no images, no scripts, no web fonts, and a
"bulletproof" table-cell button. The plain link is always printed under the button, so the
invitation still works if the button does not render.

Every value placed in HTML is escaped.
"""

from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from html import escape

from app.core.config import get_settings
from app.mail.transport import support_contact

# Brand colours from the design system (light theme), hard-coded because email clients
# do not support CSS variables.
INK, MUTED, LINE, CANVAS, PRIMARY = "#172033", "#526174", "#d8e0e8", "#f6f8fb", "#2563a6"
FONT = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _product() -> str:
    return "Next Best Action"


def _when(moment: datetime) -> str:
    """US-style date and time, labelled UTC (stored times are UTC)."""
    hour = moment.strftime("%I").lstrip("0") or "12"
    return f"{moment.strftime('%B')} {moment.day}, {moment.year} at {hour}:{moment:%M %p} UTC"


def _frame(title: str, body_html: str) -> str:
    support = support_contact()
    contact = (
        f'Questions? Contact <a href="mailto:{escape(support)}" style="color:{PRIMARY};">'
        f"{escape(support)}</a>.<br>"
        if support
        else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background:{CANVAS};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
 style="background:{CANVAS};"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
 style="max-width:600px;background:#ffffff;border:1px solid {LINE};border-radius:8px;">
<tr><td style="padding:20px 28px;border-bottom:1px solid {LINE};font-family:{FONT};
 font-size:15px;font-weight:700;color:{INK};">{escape(_product())}
 <span style="font-weight:400;color:{MUTED};"> &middot; Healthcare engagement</span></td></tr>
<tr><td style="padding:28px;font-family:{FONT};font-size:15px;line-height:1.6;color:{INK};">
{body_html}
</td></tr>
<tr><td style="padding:16px 28px;border-top:1px solid {LINE};font-family:{FONT};
 font-size:12px;line-height:1.5;color:{MUTED};">
{contact}This is a proof of concept that uses synthetic data only.
</td></tr></table></td></tr></table></body></html>"""


def _message(to: str, subject: str, text: str, html: str | None = None) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["To"] = to
    settings = get_settings()
    sender = settings.smtp_from or settings.smtp_user
    if sender:
        message["From"] = sender
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    return message


# --- One-time code ---------------------------------------------------------------------


def otp_email(to: str, name: str, code: str, minutes: int) -> EmailMessage:
    text = (
        f"Hello {name},\n\n"
        f"Your verification code is {code}.\n"
        f"It expires in {minutes} minutes and can be used once.\n\n"
        "If you did not create an account, you can ignore this message.\n"
    )
    html = _frame(
        "Your verification code",
        f"""<p style="margin:0 0 16px;">Hello {escape(name)},</p>
<p style="margin:0 0 8px;">Your verification code is</p>
<p style="margin:0 0 16px;font-size:28px;font-weight:700;letter-spacing:6px;">{escape(code)}</p>
<p style="margin:0 0 16px;color:{MUTED};">It expires in {minutes} minutes and can be used once.</p>
<p style="margin:0;color:{MUTED};">If you did not create an account, you can ignore this
message.</p>""",
    )
    return _message(to, f"{code} is your {_product()} verification code", text, html)


# --- Someone tried to register with an existing address -------------------------------


def account_exists_email(to: str) -> EmailMessage:
    base = get_settings().public_base_url.rstrip("/")
    text = (
        "Hello,\n\n"
        f"Someone tried to create a {_product()} account with this email address, but an "
        "account already exists for it.\n\n"
        f"If that was you, sign in instead: {base}/login\n\n"
        "If it was not you, you can ignore this message. Your account has not been changed "
        "and no code was issued.\n"
    )
    html = _frame(
        "Sign-up attempt",
        f"""<p style="margin:0 0 16px;">Hello,</p>
<p style="margin:0 0 16px;">Someone tried to create a {escape(_product())} account with this
email address, but an account already exists for it.</p>
<p style="margin:0 0 16px;">If that was you, <a href="{escape(base)}/login"
 style="color:{PRIMARY};">sign in instead</a>.</p>
<p style="margin:0;color:{MUTED};">If it was not you, you can ignore this message. Your account
has not been changed and no code was issued.</p>""",
    )
    return _message(to, f"Sign-up attempt for your {_product()} account", text, html)


# --- Invitation ----------------------------------------------------------------------


@dataclass
class InvitationEmail:
    to: str
    inviter_name: str
    inviter_role: str
    role_label: str
    link: str
    expires_at: datetime
    # A clinic patient invited by their care manager: plainer wording. No health
    # information either way (neither subject nor body names a condition).
    patient: bool = False


def invitation_email(data: InvitationEmail) -> EmailMessage:
    product = _product()
    expires = _when(data.expires_at)
    who = f"{data.inviter_name} ({data.inviter_role})"
    if data.patient:
        joins = f"to set up your account in the {product} patient portal"
        why = (
            "You are receiving this email because your care manager set up your account "
            "and invited this address."
        )
    else:
        joins = f"to join {product} as a {data.role_label}"
        why = (
            f"{product} recommends the next best engagement for patients and healthcare "
            "professionals. You are receiving this email because an authorized member of the "
            "platform invited this address."
        )
    text = (
        f"You have been invited by {who} {joins}.\n\n"
        f"{why}\n\n"
        f"This invitation is intended for {data.to}.\n\n"
        f"Accept the invitation:\n{data.link}\n\n"
        f"This invitation expires on {expires} and can only be used once.\n"
        "Please do not forward or share this invitation link: anyone with it could start "
        "your registration.\n\n"
        "If you were not expecting this invitation, you can ignore this email.\n"
    )
    button = f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0"
 style="margin:8px 0 24px;"><tr><td align="center" bgcolor="{PRIMARY}"
 style="border-radius:6px;background:{PRIMARY};">
<a href="{escape(data.link)}" target="_blank"
 style="display:inline-block;padding:12px 24px;font-family:{FONT};font-size:15px;
 font-weight:600;color:#ffffff;text-decoration:none;border-radius:6px;">Accept invitation</a>
</td></tr></table>"""
    html = _frame(
        "You're invited",
        f"""<p style="margin:0 0 16px;font-size:18px;font-weight:700;">You're invited to join
{escape(product)}</p>
<p style="margin:0 0 16px;">You have been invited by <strong>{escape(who)}</strong>
{escape(joins)}.</p>
<p style="margin:0 0 16px;color:{MUTED};">{escape(why)}</p>
<p style="margin:0 0 8px;">This invitation is intended for
<strong>{escape(data.to)}</strong>.</p>
{button}
<p style="margin:0 0 8px;font-size:13px;color:{MUTED};">If the button above does not work,
copy and paste this link into your browser:</p>
<p style="margin:0 0 20px;font-size:13px;word-break:break-all;">
<a href="{escape(data.link)}" style="color:{PRIMARY};">{escape(data.link)}</a></p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="padding:12px 14px;background:{CANVAS};border:1px solid {LINE};border-radius:6px;
 font-family:{FONT};font-size:13px;line-height:1.5;color:{INK};">
This invitation expires on <strong>{escape(expires)}</strong> and can only be used once.
Please do not forward or share this link. If you were not expecting it, you can ignore
this email.</td></tr></table>""",
    )
    return _message(
        data.to,
        f"{data.inviter_name} invited you to the {product} patient portal"
        if data.patient
        else f"{data.inviter_name} invited you to join {product} as a {data.role_label}",
        text, html,
    )  # fmt: skip
