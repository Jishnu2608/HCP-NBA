"""Shared helpers for the context-layer tests: a fresh synthetic world per test module (the
application's own fixture-world builder, with planted canary text), table snapshots for
read-only proofs, and a fake provider that records every request."""

import hashlib
import json

from sqlalchemy import select, text

from app.ai import fixtures
from app.ai.contracts import ContextRequest, GenerationResult
from app.ai.engine import Consumer, build_context
from app.ai.sources import source_for
from app.core.db import present_tables
from app.models import User
from app.models.enums import Role

# Tables a context build, a diagnostic or a generation may legitimately write.
BOOKKEEPING = {"ai_generation", "audit_log", "job", "rate_limit_hit"}


def fresh_world() -> fixtures.FixtureWorld:
    return fixtures._build()


def principal(db, w: fixtures.FixtureWorld, role: str) -> User:
    return db.get(User, w.users[role])


def all_categories(subject_type) -> Consumer:
    """A consumer that reads every category of the subject's source and never calls a model."""
    source = source_for(subject_type)
    return Consumer(id="test", permission=None, categories=source.category_names, model_calls=False)


def build(db, user, subject_type, subject_id=None, *, categories=(), consumer=None, **kw):
    request = ContextRequest(
        operation="test",
        subject_type=subject_type,
        subject_id=subject_id,
        categories=tuple(categories),
        **kw,
    )
    return build_context(db, user, request, consumer or all_categories(subject_type))


def table_hashes(db, exclude=BOOKKEEPING) -> dict[str, str]:
    """A digest of every row of every business table (bookkeeping tables excluded)."""
    out = {}
    for table in present_tables(db):
        if table.name in exclude:
            continue
        rows = db.execute(select(table).order_by(*table.primary_key.columns)).all()
        blob = json.dumps([list(r) for r in rows], default=str)
        out[table.name] = hashlib.sha256(blob.encode()).hexdigest()
    return out


def raw_count(db, table: str) -> int:
    return db.execute(text(f"select count(*) from {table}")).scalar()  # noqa: S608 - test only


class RecordingProvider:
    """Records every request; answers with `reply` (a JSON string) or raises `error`."""

    name, model, is_local = "fake", "fake-model", True

    def __init__(self, reply: str = "{}", error: Exception | None = None):
        self.reply, self.error, self.requests = reply, error, []

    def generate(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return GenerationResult(
            text=self.reply, provider=self.name, model=self.model, is_local=True,
            prompt_tokens=10, output_tokens=5,
        )  # fmt: skip

    def payload(self) -> str:
        return json.dumps([r.model_dump() for r in self.requests], ensure_ascii=False)


ROLES = (
    Role.CARE_MANAGER,
    Role.MEDICAL_REP,
    Role.HCP,
    Role.PATIENT,
    Role.COMPLIANCE,
    Role.ADMIN,
)
