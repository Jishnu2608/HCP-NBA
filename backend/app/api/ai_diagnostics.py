"""Admin diagnostics of the shared AI context layer over HTTP (sections A to H of the panel).

Every route requires `ai:diagnostics` (held by the administrator only). Every route is rate
limited per account and audited with codes only (reads in their own bucket); expensive routes
are POSTs (CSRF-protected like every state-changing request). Nothing here calls a model or
writes a business record; the logic lives in `app.ai.diagnostics` and `app.ai.evaluation`, which
run the real shared context code.
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
READ_BUCKET = "ai_diagnostics_read"


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


def _read(db: Session, user: User, section: str) -> None:
    """Rate limit and audit a diagnostics read (F-09): who looked at which section, no values."""
    ratelimit.consume(db, READ_BUCKET, str(user.id))
    audit.record(
        db,
        "ai_diagnostics_view",
        "ai_diagnostics",
        section,
        actor=user.username,
        actor_role=user.role,
        actor_user_id=user.id,
    )
    db.commit()


@router.get("/sources")
def sources(user: User = Depends(admins), db: Session = Depends(get_db)) -> dict:
    """A. Registered context sources, their subject types, categories and status."""
    _read(db, user, "sources")
    return jsonable_encoder(diagnostics.sources_view(db))


@router.get("/policy")
def policy(user: User = Depends(admins), db: Session = Depends(get_db)) -> dict:
    """The central field policy: every record type and the disposition of every field."""
    _read(db, user, "policy")
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
    _read(db, user, "health")
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


# --- AI context evaluation (infrastructure readiness; never registers or enables an artifact) ---

EVAL_BUCKET = "ai_evaluation"


@router.get("/evaluation")
def evaluation_last(user: User = Depends(admins), db: Session = Depends(get_db)) -> dict:
    """The last recorded evaluation (scores, counts and statuses from the audit log), or
    "not evaluated". Nothing is run."""
    _read(db, user, "evaluation")
    from app.ai.evaluation import runner

    last = runner.last(db)
    return jsonable_encoder({"last": last, "readiness": last["readiness"] if last else
                             "not_evaluated"})  # fmt: skip


@router.post("/evaluation/run")
def evaluation_run(user: User = Depends(admins), db: Session = Depends(get_db)) -> dict:
    """Runs the predefined synthetic scenarios through the real shared services on isolated
    fixture worlds, scores them with the fixed rubric and records the outcome (codes only).
    Accepts no input: no SQL, sources, payloads, policies or free text can be supplied."""
    ratelimit.consume(db, EVAL_BUCKET, str(user.id))
    from app.ai.evaluation import runner

    result = runner.run(db, user)
    return jsonable_encoder({"result": result, "export": runner.export(result)})
