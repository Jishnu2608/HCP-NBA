"""HTTP-level protections: CSRF, origin checks, security headers, safe error bodies and
log redaction. Installed on the app in main.py.

CSRF uses the signed double-submit pattern. The server sets a random, signed token in a
readable cookie (`nba_csrf`); the web app copies it into an `X-CSRF-Token` header on every
state-changing request. Another site can make the browser send the cookie but cannot read
it, so it cannot produce the header. Session cookies are also SameSite=Lax, and when the
browser sends an Origin (or Referer) it must be this site or an allowed origin.
"""

import base64
import hashlib
import hmac
import logging
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.security import derived_key

CSRF_COOKIE = "nba_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

log = logging.getLogger("nba.http")


# --- CSRF ------------------------------------------------------------------------------


def _sign(nonce: str) -> str:
    return hmac.new(derived_key("csrf"), nonce.encode(), hashlib.sha256).hexdigest()[:32]


def new_csrf_token() -> str:
    nonce = secrets.token_urlsafe(24)
    return f"{nonce}.{_sign(nonce)}"


def csrf_token_valid(token: str | None) -> bool:
    if not token or token.count(".") != 1:
        return False
    nonce, signature = token.split(".")
    return hmac.compare_digest(signature, _sign(nonce))


def _host_of(url: str) -> str:
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)", url)
    return match.group(1).lower() if match else ""


def origin_allowed(request: Request) -> bool:
    """True when the request comes from this site or a configured origin. Requests without
    Origin and Referer (non-browser clients) rely on the CSRF token alone: they cannot ride
    on a victim's cookies."""
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source or source == "null":
        return source != "null"
    host = _host_of(source)
    if host and host == (request.headers.get("host") or "").lower():
        return True
    allowed = {_host_of(o.strip()) for o in get_settings().allowed_origins.split(",") if o.strip()}
    return host in allowed


def _refuse(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=403, content={"detail": {"code": "csrf_failed", "message": message}}
    )


# --- Security headers ------------------------------------------------------------------


def inline_script_hashes(index: Path) -> list[str]:
    """CSP hashes of the inline scripts in the built index.html (the theme bootstrap)."""
    if not index.exists():
        return []
    html = index.read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S)
    return [
        "'sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + "'"
        for s in scripts
        if s.strip()
    ]


def security_headers(script_hashes: list[str]) -> dict[str, str]:
    script_src = " ".join(["'self'", *script_hashes])
    headers = {
        "Content-Security-Policy": (
            "default-src 'self'; "
            f"script-src {script_src}; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        ),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        # Invitation links carry a token in the address: never leak it in a Referer.
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    return headers


HSTS = "max-age=31536000; includeSubDomains"


# --- Log redaction ---------------------------------------------------------------------


class RedactInviteTokens(logging.Filter):
    """Keeps invitation tokens out of the access log (`/invite/<token>` page loads)."""

    pattern = re.compile(r"(/invite/)[^\s?\"]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                self.pattern.sub(r"\1[redacted]", a) if isinstance(a, str) else a
                for a in record.args
            )
        elif isinstance(record.msg, str):
            record.msg = self.pattern.sub(r"\1[redacted]", record.msg)
        return True


# --- Installation ----------------------------------------------------------------------


def install(app: FastAPI, index_html: Path) -> None:
    headers = security_headers(inline_script_hashes(index_html))
    logging.getLogger("uvicorn.access").addFilter(RedactInviteTokens())

    @app.middleware("http")
    async def protect(request: Request, call_next):
        settings = get_settings()
        csrf_cookie = request.cookies.get(CSRF_COOKIE)
        if request.url.path.startswith("/api/") and request.method not in SAFE_METHODS:
            if not origin_allowed(request):
                return _refuse("This request did not come from this site.")
            header = request.headers.get(CSRF_HEADER)
            if not (
                csrf_token_valid(csrf_cookie)
                and header
                and hmac.compare_digest(header, csrf_cookie or "")
            ):
                return _refuse("Your session security check failed. Reload the page and try again.")
        response = await call_next(request)
        for name, value in headers.items():
            response.headers.setdefault(name, value)
        if not settings.is_local:
            response.headers.setdefault("Strict-Transport-Security", HSTS)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if not csrf_token_valid(csrf_cookie):
            response.set_cookie(
                CSRF_COOKIE,
                new_csrf_token(),
                httponly=False,  # the web app must read it to echo it in the header
                samesite="lax",
                secure=settings.cookie_secure,
                path="/",
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default body echoes the submitted input, which may contain a password.
        fields = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"),
                "message": err.get("msg", "Invalid value"),
            }
            for err in exc.errors()
        ]
        unknown = [f["field"] for f, e in zip(fields, exc.errors(), strict=True)
                   if e.get("type") == "extra_forbidden"]  # fmt: skip
        message = "Check the highlighted fields."
        if unknown:
            message = f"Unexpected field: {', '.join(unknown)}."
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": "invalid_request", "message": message, "fields": fields}},
        )

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": "server_error",
                    "message": "Something went wrong on our side. Please try again.",
                }
            },
        )
