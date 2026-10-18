"""One recommendation and its decision trace (`services.recommendations.recommendation_detail`).

Model-eligible: the action, channel, status and schedule, the coded reasons (kind and code
only, never the reason text), each candidate considered with its eligibility and gate codes,
and the response outcome. Scores, rationale text, wording drafts, the audit trail and the
person's identity stay out. A gate-outcome reader (Compliance) gets the same structure without
identity, because the read service already removes it.
"""

import json
from datetime import date

from sqlalchemy.orm import Session

from app.ai.contracts import ContextRequest, SubjectType
from app.ai.policy import project
from app.ai.sources.base import Builder, Category, ContextSource
from app.models import User
from app.services import recommendations
from app.services.errors import NotFound

SOURCE = "recommendation"


def _date(value) -> date | None:
    return None if value is None else date.fromisoformat(str(value)[:10])


def read(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    try:
        nba_id = int(request.subject_id)
    except ValueError:
        raise NotFound("Recommendation not found") from None
    detail = recommendations.recommendation_detail(db, user, nba_id)
    raw = json.loads(detail.model_dump_json())
    model = b.absorb(project("recommendation", raw)).model
    if detail.target_id:
        b.identifiers.append(detail.target_id)
    if detail.target_name:
        b.identifiers += [p for p in detail.target_name.split() if len(p) >= 3 and p.isalpha()]
    b.as_of = detail.as_of_date
    src = f"{SOURCE}.detail"

    when = f", scheduled {model['scheduled_for']}" if model.get("scheduled_for") else ""
    content = f", content {model['content_id']}" if model.get("content_id") else ""
    status = str(model["status"]).replace("_", " ")
    b.add(
        "decision",
        f"Recommendation for a {str(model['target_type']).lower()}: {model['action_label']} by "
        f"{model['channel_label']}{content}{when}; status {status}.",
        src, nba_id, _date(model.get("scheduled_for")), field_path="recommendation.status",
    )  # fmt: skip
    b.facts["status"] = str(model["status"])
    b.facts["reason_codes"] = sorted({r["code"] for r in model["reason_codes"]})
    b.facts["gate_codes"] = sorted({g for c in model["candidates"] for g in c["gate_failures"]})
    b.mark("decision", 1)

    for r in model["reason_codes"]:
        b.add(
            "reason", f"Reason ({r['kind']}): {r['code']}.", f"{SOURCE}.reason_codes", nba_id,
            field_path="reason.kind,code", as_of=detail.as_of_date,
        )  # fmt: skip
    b.mark("reason", len(model["reason_codes"]))

    for c in model["candidates"]:
        gates = ", ".join(c["gate_failures"]) or "none"
        state = "chosen" if c["chosen"] else ("eligible" if c["eligible"] else "not eligible")
        rank = f"#{c['rank']} " if c.get("rank") is not None else ""
        b.add(
            "candidate",
            f"Option {rank}{c['action_label']} by {c['channel_label']}: {state}; gate failures: "
            f"{gates}.",
            f"{SOURCE}.candidates", nba_id, field_path="candidate.eligible,gate_failures",
            as_of=detail.as_of_date,
        )  # fmt: skip
    b.mark("candidate", len(model["candidates"]))

    resp = model.get("response")
    if resp:
        b.add(
            "response",
            f"Response: {str(resp['outcome']).replace('_', ' ')} (sent {str(resp['sent_at'])[:10]}"
            f" by {resp['channel']}).",
            f"{SOURCE}.response", nba_id, _date(resp.get("at") or resp.get("sent_at")),
        )  # fmt: skip
    b.mark("response", 1 if resp else 0)


SOURCE_DEF = ContextSource(
    id=SOURCE,
    domain="NBA recommendations and decision traces",
    subject_types=(SubjectType.RECOMMENDATION,),
    categories=(
        Category(
            "decision",
            "recommendation",
            "Action, channel, content id, schedule, status",
            terms=("recommendation", "recommendations", "decision"),
        ),
        Category("reason", "reason", "Coded reasons (kind and code)", terms=("reason", "reasons")),
        Category(
            "candidate",
            "candidate",
            "Options considered: eligibility and gate codes",
            terms=("option", "options", "alternative", "alternatives", "gate", "gates"),
        ),
        Category("response", "response", "Recorded response outcome"),
    ),
    read=read,
    read_services=("services.recommendations.recommendation_detail",),
)
