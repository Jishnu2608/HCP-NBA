"""Admin diagnostics of the shared AI context layer over HTTP (sections A to H of the panel).

Every route requires `ai:diagnostics` (held by the administrator only). Expensive routes are
POSTs (CSRF-protected like every state-changing request), rate limited per account and audited
with codes only. Nothing here calls a model or writes a business record; the logic lives in
`app.ai.diagnostics`, which runs the real shared context code.
"""

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app import audit
from app.ai import diagnostics
from app.api.deps import require_permission
from app.core import ratelimit
from app.core.db import get_db
from app.core.permissions import Permission
from app.models import User

router = APIRouter(prefix="/api/ai/diagnostics", tags=["ai-diagnostics"])

admins = require_permission(Permission.AI_DIAGNOSTICS)
BUCKET = "ai_diagnostics"


def _record(db: Session, user: User, section: str, **detail) -> None:
    """Audit a diagnostic run: the section and fixed codes, never a value or record id."""
    audit.record(
        db,
        "ai_diagnostics_run",
        "ai_diagnostics",
        section,
        actor=user.username,
        actor_role=user.role,
        actor_user_id=user.id,
        detail={k: v for k, v in detail.items() if v is not None},
    )
    db.commit()


def _limit(db: Session, user: User) -> None:
    ratelimit.consume(db, BUCKET, str(user.id))


@router.get("/sources")
def sources(user: User = Depends(admins), db: Session = Depends(get_db)) -> dict:
    """A. Registered context sources, their subject types, categories and status."""
    return jsonable_encoder(diagnostics.sources_view(db))


@router.get("/policy")
def policy(user: User = Depends(admins)) -> dict:
    """The central field policy: every record type and the disposition of every field."""
    return diagnostics.policy_view()


@router.post("/context")
def context(
    probe: diagnostics.ContextProbe, user: User = Depends(admins), db: Session = Depends(get_db)
) -> dict:
    """B, C, D. Build a context through the real engine and explain every decision."""
    _limit(db, user)
    result = diagnostics.context_probe(db, user, probe)
    db.rollback()  # a preview reads; nothing it touched is kept
    failed = next((c["code"] for c in result["checks"] if c["result"] == "fail"), None)
    _record(
        db, user, "context", mode=probe.mode, subject_type=probe.subject_type.value,
        role=probe.role if probe.mode == "fixture" else None, outcome=failed or "built",
    )  # fmt: skip
    return jsonable_encoder(result)


@router.post("/safety")
def safety(
    probe: diagnostics.SafetyProbe | None = None,
    user: User = Depends(admins),
    db: Session = Depends(get_db),
) -> dict:
    """E. The shared deterministic checks against fixed adversarial cases (and optional text)."""
    _limit(db, user)
    result = diagnostics.safety_suite(probe)
    _record(db, user, "safety", passed=result["passed"], failed=result["failed"])
    return result


@router.post("/aggregate")
def aggregate(user: User = Depends(admins), db: Session = Depends(get_db)) -> dict:
    """F. Multi-subject, collection and aggregate contexts on the fixture world."""
    _limit(db, user)
    result = diagnostics.aggregate_suite()
    _record(db, user, "aggregate", complete=result["multi_subject"]["complete"])
    return jsonable_encoder(result)


@router.get("/health")
def health(user: User = Depends(admins), db: Session = Depends(get_db)) -> dict:
    """G. Read-service probes, AI tables, provider configuration (no network), jobs, telemetry."""
    return jsonable_encoder(diagnostics.health_view(db, user))


@router.post("/provider-check")
def provider_check(user: User = Depends(admins), db: Session = Depends(get_db)) -> dict:
    """One loopback request to the configured local provider (`/api/tags`), on demand only."""
    _limit(db, user)
    from app.ai import health as provider_health

    result = provider_health.provider_status(probe=True)
    _record(db, user, "provider_check", state=result["state"])
    return result


@router.post("/report")
def report(user: User = Depends(admins), db: Session = Depends(get_db)) -> dict:
    """H. A sanitized diagnostic report: versions, checks, results and failures; no values."""
    _limit(db, user)
    result = diagnostics.report(db, user)
    _record(db, user, "report", failures=len(result["failures"]))
    return jsonable_encoder(result)
