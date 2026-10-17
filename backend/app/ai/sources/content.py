"""Content sources: one content version, and its lineage with scoped reference counts.

Metadata (audience, channels, MLR status, validity, version) is model-eligible. The governed
wording itself (title, body, claims) is PENDING_REVIEW: whether approved MLR text may be given
to a model is a governance decision, so it never enters a context today. Visibility of every
version follows `services.content.readable` (audience, approval, authorship, delivery).
"""

import json
from datetime import date

from sqlalchemy.orm import Session

from app.ai.contracts import ContextRequest, SubjectType
from app.ai.policy import project
from app.ai.sources.base import Builder, Category, ContextSource
from app.core import clock
from app.models import User
from app.services import content, content_lineage


def _date(value) -> date | None:
    return None if value is None else date.fromisoformat(str(value)[:10])


def read_version(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    c = content.content_version(db, user, request.subject_id)
    b.as_of = clock.get_today(db)
    model = b.absorb(project("content", json.loads(c.model_dump_json()))).model
    window = (
        f"effective {model['effective_date'] or 'not set'}, "
        f"expires {model['expiry_date'] or 'not set'}"
    )
    usable = "usable now" if model["usable"] else "not usable now"
    b.add(
        "content",
        f"Content {model['content_id']} version {model['version']}: audience "
        f"{str(model['audience']).lower()}, {str(model['action_type']).replace('_', ' ')}, topic "
        f"{model['topic']}; channels {', '.join(model['channels']) or 'none'}; MLR status "
        f"{str(model['mlr_status']).replace('_', ' ')}; {window}; {usable}.",
        "content.version", model["content_id"], _date(model.get("effective_date")),
        version=model["version"], field_path="content.metadata",
    )  # fmt: skip
    b.mark("content", 1)


def read_lineage(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    lineage = content_lineage.content_lineage(db, user, request.subject_id)
    b.as_of = clock.get_today(db)
    versions = [b.absorb(project("content_version_ref", v.model_dump())).model
                for v in lineage.lineage_versions]  # fmt: skip
    for v in versions:
        state = "this version" if v["current"] else "other version"
        b.add(
            "version",
            f"Version {v['version']} ({state}): {str(v['mlr_status']).replace('_', ' ')}, "
            f"effective {v['effective_date'] or 'not set'}, expires "
            f"{v['expiry_date'] or 'not set'}, {'usable' if v['usable'] else 'not usable'}.",
            "content_lineage.versions", v["content_id"], _date(v.get("effective_date")),
            version=v["version"],
        )  # fmt: skip
    b.mark("version", len(versions))
    if lineage.hidden_versions:
        # Counted, never described: the reader may not see those versions.
        b.facts["hidden_versions"] = lineage.hidden_versions
    refs = [b.absorb(project("content_reference_count", r.model_dump())).model
            for r in lineage.references]  # fmt: skip
    for r in refs:
        b.add(
            "reference",
            f"{r['kind'].capitalize()}s referencing this content with status "
            f"{str(r['status']).replace('_', ' ')}: {r['count']} (within your scope).",
            "content_lineage.references", f"{r['kind']}:{r['status']}",
        )  # fmt: skip
    b.mark("reference", len(refs))


VERSION = ContextSource(
    id="content",
    domain="Approved content (one version)",
    subject_types=(SubjectType.CONTENT,),
    categories=(
        Category("content", "content", "Audience, channels, MLR status, validity, version"),
    ),
    read=read_version,
    read_services=("services.content.content_version",),
)
LINEAGE = ContextSource(
    id="content_lineage",
    domain="Content lineage and references (aggregate)",
    subject_types=(SubjectType.CONTENT_LINEAGE,),
    categories=(
        Category("version", "content_version_ref", "Visible versions and their lifecycle"),
        Category(
            "reference",
            "content_reference_count",
            "Scoped reference counts by status",
            freshness="current",
        ),
    ),
    read=read_lineage,
    read_services=("services.content_lineage.content_lineage",),
    principal_dependent=True,
)
