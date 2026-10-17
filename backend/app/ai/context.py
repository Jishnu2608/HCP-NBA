"""The context service entry point kept for existing callers (the orchestrator, the Case Brief).

`build(db, user, request, permission=..., allowed_kinds=...)` is a thin wrapper over the shared
engine (`app.ai.engine.build_context`): it describes the caller as a consumer (its permission
and allowlist) and returns the engine's `AuthorizedContext`. For a patient subject the engine
reads the registered `patient_360` source, which writes exactly the items the Case Brief has
always received (frozen by `tests/test_context_compatibility.py`).

New code calls the engine directly with a `Consumer`. Any doubt fails closed: an exception
here means no context, so no model call. A model never touches the database, a query, or an
application object; it sees only `model_items` text.
"""

from sqlalchemy.orm import Session

from app.ai.contracts import AuthorizedContext, ContextRequest, Sensitivity
from app.ai.engine import GLOBAL_MAX_CHARS, GLOBAL_MAX_ITEMS, Consumer, build_context
from app.ai.sources.patient import NOT_IDENTIFYING, NOTE_LIMIT, OUTREACH, TOUCH_LIMIT
from app.models import User


def build(
    db: Session,
    user: User,
    request: ContextRequest,
    *,
    permission: str,
    allowed_kinds: tuple[str, ...],
    max_items: int = GLOBAL_MAX_ITEMS,
    max_chars: int = GLOBAL_MAX_CHARS,
) -> AuthorizedContext:
    """Authorised, minimised context for one consumer. Raises `Forbidden` or `NotFound` (the read
    services' own errors) or `UntrustedContentError`; never returns partial data silently: a
    partial context says so in `complete` and its category states."""
    consumer = Consumer(
        id=request.operation,
        permission=permission,
        categories=tuple(allowed_kinds),
        model_calls=True,
        max_items=max_items,
        max_chars=max_chars,
    )
    return build_context(db, user, request, consumer)


__all__ = [
    "NOTE_LIMIT",
    "NOT_IDENTIFYING",
    "OUTREACH",
    "TOUCH_LIMIT",
    "Sensitivity",
    "build",
]
