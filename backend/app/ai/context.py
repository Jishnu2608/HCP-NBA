"""The shared context service: the only way an AI operation obtains data.

For one acting user, one subject and one operation it:

1. checks the operation's permission on the server (`core.permissions.can`);
2. reads the subject through the scoped read service (`services.patients.patient_profile`),
   which applies the read permission and the record scope (out of scope raises `NotFound`,
   exactly as the API answers);
3. classifies every field (`context_policy`), refusing any field nobody has classified;
4. writes each allowlisted fact as a `ContextItem` with its provenance (source, record id,
   recorded date), from the model-eligible fields only;
5. keeps free text for the authorised reader (`ReaderNote`, verbatim, one fixed label) and
   records it as withheld, together with the subject's identifiers, so later checks can prove
   none of it reaches a model;
6. reduces the model's view to the operation's allowlisted kinds (`model_items`, which refuses
   loudly rather than dropping silently);
7. fingerprints the facts, so a stored result can be recognised as stale.

Any doubt fails closed: an exception here means no context, so no model call. A model never
touches the database, a query, or an application object; it sees only `model_items` text.
"""

import hashlib
import json
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.ai import context_policy as policy
from app.ai.contracts import (
    AuthorizedContext,
    ContextItem,
    ContextRequest,
    ReaderNote,
    Sensitivity,
)
from app.core.engine_config import get_config
from app.core.permissions import can
from app.models import User
from app.models.enums import ConsentPurpose
from app.services import patients
from app.services.errors import Forbidden

TOUCH_LIMIT = 5  # most recent outreach records a model may read
NOTE_LIMIT = 10  # most recent free-text entries shown to the reader beside the result
OUTREACH = ("email", "sms", "phone", "portal")
SOURCE = "patient_360"


def _date(value) -> date | None:
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _fingerprint(request: ContextRequest, as_of: date, items: list[ContextItem]) -> str:
    blob = json.dumps(
        {
            "op": request.operation,
            "subject": [request.subject_type, request.subject_id],
            "as_of": as_of.isoformat(),
            "items": [[i.id, i.kind, i.text] for i in items],
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


# Name parts that are ordinary words in staff display names and titles, never identifying.
NOT_IDENTIFYING = frozenset(
    {"care", "manager", "team", "patient", "doctor", "nurse", "clinic", "health", "demo"}
)


def _identifiers(profile) -> list[str]:
    """Strings that identify the subject or the people around them. They never go to a model,
    and model text containing one is rejected. Name parts are kept when they are words of at
    least three letters that are not ordinary role words."""
    values = [profile.patient_id, profile.city]
    names = [profile.name]
    names += [m.name for m in profile.care_team] + [m.name for m in profile.care_managers]
    for name in names:
        values += [
            p
            for p in (name or "").replace(",", " ").split()
            if len(p) >= 3 and p.isalpha() and p.lower() not in NOT_IDENTIFYING
        ]
    return sorted({v for v in values if v})


def _patient_items(
    profile, model: dict, as_of: date, window: int
) -> tuple[list[ContextItem], dict]:
    """Every structured fact as an item, plus the same facts typed for deterministic text.
    Item text is written only from `model` (the classified, model-eligible view); the profile
    supplies provenance ids and server-side filters (a discontinued therapy is left out)."""
    items: list[ContextItem] = []
    pid = profile.patient_id

    def add(kind: str, n: int, text: str, source: str, source_id, recorded=None) -> str:
        cid = f"ctx:{kind}:{n}"
        items.append(
            ContextItem(
                id=cid,
                kind=kind,
                text=text,
                source=f"{SOURCE}.{source}",
                source_id=str(source_id),
                recorded_at=recorded,
            )  # fmt: skip
        )
        return cid

    facts: dict = {"age": model["age"], "therapies": [], "touches": [], "nba": None}
    facts["patient_item"] = add("patient", 1, f"Patient, age {model['age']}.", "profile", pid)

    fills_by_therapy: dict[int, list[dict]] = {}
    for f in model.get("fills") or []:
        fills_by_therapy.setdefault(f["therapy_id"], []).append(f)
    n_rx = n_pdc = 0
    for raw, t in zip(profile.therapies, model.get("therapies") or [], strict=True):
        if raw.persistence is not None and raw.persistence.discontinued:
            continue
        drug = t["drug_name"]
        last = _date(t.get("last_fill_date"))
        fills = sorted(fills_by_therapy.get(raw.therapy_id, []), key=lambda f: f["fill_date"])
        supply = fills[-1]["days_supply"] if fills else t.get("days_supply")
        entry: dict = {"drug": drug, "last": last, "supply": supply, "due": None, "pdc": None}
        if last and supply:
            due = last + timedelta(days=int(supply))
            entry["due"] = due
            status = (
                f"Next refill due {due.isoformat()} (not yet due)."
                if due > as_of
                else f"Next refill was due {due.isoformat()}; not yet filled."
            )
            n_rx += 1
            entry["rx_item"] = add(
                "rx", n_rx,
                f"Therapy: {drug}, {supply}-day supply. Last refill {last.isoformat()}. {status}",
                "therapies", raw.therapy_id, last,
            )  # fmt: skip
        if t.get("pdc") is not None:
            entry["pdc"] = float(t["pdc"])
            n_pdc += 1
            entry["pdc_item"] = add(
                "pdc", n_pdc,
                f"Proportion of days covered for {drug} over the last {window} days: "
                f"{float(t['pdc']):.2f}.",
                "therapies", raw.therapy_id, as_of,
            )  # fmt: skip
        facts["therapies"].append(entry)

    # Outreach: newest first in the record; the model reads the latest few, oldest first.
    rows = list(zip(profile.interactions, model.get("interactions") or [], strict=True))
    for n, (raw, r) in enumerate(reversed(rows[:TOUCH_LIMIT]), 1):
        day = _date(r["ts"])
        outcome = str(r["outcome"]).replace("_", " ")
        text = f"{day.isoformat()} {r['channel_label']} {r['type_label'].lower()}; {outcome}."
        cid = add("touch", n, text, "interactions", raw.id, day)
        facts["touches"].append(
            {"day": day, "text": text, "item": cid, "outcome": str(r["outcome"])}
        )

    nba = model.get("open_nba")
    if nba:
        status = str(nba["status"]).replace("_", " ")
        scheduled = _date(nba.get("scheduled_for"))
        when = f", scheduled {scheduled.isoformat()}" if scheduled and scheduled > as_of else ""
        content = f", approved content {nba['content_id']}" if nba.get("content_id") else ""
        what = f"{nba['action_label']} by {nba['channel_label']}"
        text = f"Open recommendation: {what}{content}{when}, {status}."
        cid = add("nba", 1, text, "open_nba", profile.open_nba.id, scheduled)
        facts["nba"] = {
            "action": nba["action_label"], "channel": nba["channel_label"],
            "content_id": nba.get("content_id"), "status": status, "item": cid,
        }  # fmt: skip

    # Consent: an item the server cites in its own text. It is never model-eligible for an
    # operation that keeps consent out of the model's view (see the operation's allowlist).
    granted = sorted(
        {
            c.channel
            for c in profile.consents
            if c.granted
            and c.in_effect
            and c.purpose == ConsentPurpose.OUTREACH
            and c.channel in OUTREACH
        }
    )
    if profile.consents:
        text = "Outreach consent in effect: " + (", ".join(granted) or "none") + "."
        facts["consent_item"] = add("consent", 1, text, "consents", pid, as_of)
    facts["consent"] = granted if profile.consents else None

    absent = [
        name
        for name, missing in (
            ("open recommendation", facts["nba"] is None),
            ("outreach", not facts["touches"]),
            ("refill record", n_rx == 0),
            ("proportion of days covered", n_pdc == 0),
        )
        if missing
    ]
    facts["absent"] = absent
    if absent:
        facts["absent_item"] = add(
            "absent", 1, "Not recorded for this patient: " + "; ".join(absent) + ".", "profile", pid
        )
    return items, facts


def _reader_notes(profile) -> list[ReaderNote]:
    """Care notes and interaction notes, verbatim, newest first, each with the same label."""
    notes: list[ReaderNote] = []
    for n in profile.health.get("notes") or []:
        if n.get("text"):
            notes.append(
                ReaderNote(
                    text=n["text"],
                    source=f"{SOURCE}.health.notes",
                    source_id=str(n["id"]),
                    recorded_at=_date(n.get("created_at")),
                )  # fmt: skip
            )
    for i in profile.interactions:
        for field in ("note", "outcome_reason"):
            text = getattr(i, field)
            if text:
                notes.append(
                    ReaderNote(
                        text=text,
                        source=f"{SOURCE}.interactions.{field}",
                        source_id=str(i.id),
                        recorded_at=_date(i.ts),
                    )  # fmt: skip
                )
    notes.sort(key=lambda n: n.recorded_at or date.min, reverse=True)
    return notes[:NOTE_LIMIT]


def build(
    db: Session,
    user: User,
    request: ContextRequest,
    *,
    permission: str,
    allowed_kinds: tuple[str, ...],
) -> AuthorizedContext:
    """Authorised, minimised context for one operation. Raises `Forbidden` or `NotFound`
    (the read services' own errors) or `UntrustedContentError`; never returns partial data."""
    if not can(user, permission):
        raise Forbidden("Your account cannot use this operation.")
    if request.subject_type != "patient":
        raise Forbidden("Unsupported subject.")
    profile = patients.patient_profile(db, user, request.subject_id)
    dump = json.loads(profile.model_dump_json())
    model_view, withheld = policy.split_patient_360(dump)
    items, facts = _patient_items(
        profile, model_view, profile.as_of_date, int(get_config(db, "pdc_window_days"))
    )
    eligible = [i for i in items if i.kind in allowed_kinds]
    model_items = policy.model_items(eligible, allowed_kinds)
    notes = _reader_notes(profile)
    vocabulary = {t.drug_name for t in profile.therapies}
    vocabulary |= {
        m.get("drug_name") for m in profile.health.get("medications") or [] if m.get("drug_name")
    }
    return AuthorizedContext(
        request=request,
        as_of=profile.as_of_date,
        items=items,
        model_items=model_items,
        reader_notes=notes,
        facts=facts,
        withheld_texts=withheld + [n.text for n in notes],
        identifiers=_identifiers(profile),
        vocabulary=sorted(vocabulary),
        fingerprint=_fingerprint(request, profile.as_of_date, items),
    )


__all__ = ["build", "Sensitivity"]
