import hashlib
import hmac
import secrets

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=_N, r=_R, p=_P)
    return hmac.compare_digest(digest.hex(), digest_hex)


def derived_key(label: str) -> bytes:
    """A key for one purpose (one-time codes, CSRF, fingerprints), derived from the signing
    secret so each use has its own key without another secret to manage."""
    from app.core.config import get_settings

    master = get_settings().secret("jwt_secret").encode()
    return hmac.new(master, f"nba:{label}".encode(), hashlib.sha256).digest()


def new_token() -> str:
    """An opaque, unguessable token (256 bits) for sessions and invitation links."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """What is stored instead of a session or invitation token."""
    return hashlib.sha256(token.encode()).hexdigest()


def fingerprint(value: str) -> str:
    """A keyed, non-reversible marker for a value that must not be stored in clear (for
    example the email of a failed sign-in, so repeated attempts can be correlated)."""
    return hmac.new(derived_key("fingerprint"), value.encode(), hashlib.sha256).hexdigest()[:16]
