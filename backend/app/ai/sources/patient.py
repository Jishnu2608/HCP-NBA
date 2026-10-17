"""Patient 360 for a staff reader (`services.patients.patient_profile`).

The core categories (patient, rx, pdc, touch, nba, consent, absent) are written exactly as the
Case Brief has always received them: same item ids, text, provenance and order, and the same
Patient 360 projection (`context_policy.split_patient_360`, which withholds the whole health
block). Health categories (care requests, conditions, medications as recorded) are built only
when a consumer asks for them, through the central field policy record by record.

Reads never write: `refresh=False`, and stale adherence figures are reported as `stale`.
"""

import json
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.ai import context_policy as legacy
from app.ai.contracts import ContextRequest, ReaderNote, SubjectType
from app.ai.policy import is_vocabulary_drug, project
from app.ai.policy.engine import UNLISTED_MEDICATION
from app.ai.sources.base import Builder, Category, ContextSource
from app.core.engine_config import get_config
from app.models import User
from app.models.enums import ConsentPurpose
from app.services import patients

TOUCH_LIMIT = 5  # most recent outreach records a model may read
NOTE_LIMIT = 10  # most recent free-text entries shown to the reader beside the result
OUTREACH = ("email", "sms", "phone", "portal")
SOURCE = "patient_360"
# Name parts that are ordinary words in staff display names and titles, never identifying.
NOT_IDENTIFYING = frozenset(
    {"care", "manager", "team", "patient", "doctor", "nurse", "clinic", "health", "demo"}
)
CORE = ("patient", "rx", "pdc", "touch", "nba", "consent", "absent")
HEALTH = ("care_request", "condition", "medication_record")


def _date(value) -> date | None:
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def identifiers(profile) -> list[str]:
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


def _drug(name: str, measure) -> str:
    """The model-visible medicine name: approved vocabulary only (POL-02)."""
    return name if measure is not None and is_vocabulary_drug(name) else UNLISTED_MEDICATION


def core_items(b: Builder, profile, model: dict, as_of: date, window: int) -> None:
    """Every structured Patient 360 fact as an item, plus the same facts typed for deterministic
    text. Item text is written only from `model` (the classified, model-eligible view); the
    profile supplies provenance ids and server-side filters (a discontinued therapy is left out).
    """
    pid = profile.patient_id

    def add(kind: str, text: str, source: str, source_id, recorded=None) -> str:
        return b.add(kind, text, f"{SOURCE}.{source}", source_id, recorded)

    facts: dict = {"age": model["age"], "therapies": [], "touches": [], "nba": None}
    facts["patient_item"] = add("patient", f"Patient, age {model['age']}.", "profile", pid)

    fills_by_therapy: dict[int, list[dict]] = {}
    for f in model.get("fills") or []:
        fills_by_therapy.setdefault(f["therapy_id"], []).append(f)
    n_rx = n_pdc = 0
    for raw, t in zip(profile.therapies, model.get("therapies") or [], strict=True):
        if raw.persistence is not None and raw.persistence.discontinued:
            continue
        drug = _drug(t["drug_name"], raw.measure)
        if drug != t["drug_name"]:
            b.withheld.append(t["drug_name"])
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
                "rx",
                f"Therapy: {drug}, {supply}-day supply. Last refill {last.isoformat()}. {status}",
                "therapies", raw.therapy_id, last,
            )  # fmt: skip
        if t.get("pdc") is not None:
            entry["pdc"] = float(t["pdc"])
            n_pdc += 1
            entry["pdc_item"] = add(
                "pdc",
                f"Proportion of days covered for {drug} over the last {window} days: "
                f"{float(t['pdc']):.2f}.",
                "therapies", raw.therapy_id, as_of,
            )  # fmt: skip
        facts["therapies"].append(entry)

    # Outreach: newest first in the record; the model reads the latest few, oldest first.
    rows = list(zip(profile.interactions, model.get("interactions") or [], strict=True))
    for raw, r in reversed(rows[:TOUCH_LIMIT]):
        day = _date(r["ts"])
        outcome = str(r["outcome"]).replace("_", " ")
        text = f"{day.isoformat()} {r['channel_label']} {r['type_label'].lower()}; {outcome}."
        cid = add("touch", text, "interactions", raw.id, day)
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
        cid = add("nba", text, "open_nba", profile.open_nba.id, scheduled)
        facts["nba"] = {
            "action": nba["action_label"], "channel": nba["channel_label"],
            "content_id": nba.get("content_id"), "status": status, "item": cid,
        }  # fmt: skip

    # Consent: an item the server cites in its own text, never model-visible (category model
    # flag is False), whatever a consumer's allowlist says.
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
        facts["consent_item"] = add("consent", text, "consents", pid, as_of)
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
            "absent", "Not recorded for this patient: " + "; ".join(absent) + ".", "profile", pid
        )
    b.facts.update(facts)
    total_touches = len(profile.interactions)
    for name, total in (
        ("patient", 1),
        ("rx", n_rx),
        ("pdc", n_pdc),
        ("touch", total_touches),
        ("nba", 1 if facts["nba"] else 0),
        ("consent", len(profile.consents)),
        ("absent", 1 if absent else 0),
    ):
        b.mark(name, total)


def reader_notes(profile) -> list[ReaderNote]:
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


def health_items(b: Builder, health: dict, as_of: date) -> None:
    """Structured care-request, condition and medication facts from the health record, field by
    field through the central policy. Reasons, resolutions, notes and typed labels stay out."""
    proj = b.absorb(project("health", health), prefix="health.")
    model = proj.model
    if "care_request" in b.wanted:
        requests = model.get("requests") or []
        for r, raw in zip(requests, health.get("requests") or [], strict=True):
            due = f", due {r['due_date']}" if r.get("due_date") else ""
            overdue = " (overdue)" if r.get("overdue") else ""
            text = (
                f"Care request: {str(r['type']).replace('_', ' ')}, "
                f"{str(r['status']).replace('_', ' ')}{due}{overdue}; "
                f"opened {str(r['created_at'])[:10]}."
            )
            b.add(
                "care_request", text, f"{SOURCE}.health.requests", raw["id"],
                _date(r.get("created_at")), field_path="care_request.type,status,due_date",
            )  # fmt: skip
        b.mark("care_request", len(requests))
    if "condition" in b.wanted:
        conditions = model.get("conditions") or []
        for c, raw in zip(conditions, health.get("conditions") or [], strict=True):
            code = "other condition" if c["condition"] == "other" else c["condition"]
            text = f"Condition record: {str(code).replace('_', ' ')}, {c['status']}."
            b.add(
                "condition", text, f"{SOURCE}.health.conditions", raw["id"],
                _date(c.get("confirmed_at") or c.get("reported_at")),
                field_path="condition.condition,status",
            )  # fmt: skip
        b.mark("condition", len(conditions))
    if "medication_record" in b.wanted:
        meds = model.get("medications") or []
        for m, raw in zip(meds, health.get("medications") or [], strict=True):
            text = (
                f"Medication record: {m['drug_name']}, {m['review_status']} "
                f"({str(m['origin']).replace('_', ' ')})."
            )
            b.add(
                "medication_record", text, f"{SOURCE}.health.medications", raw["therapy_id"],
                _date(m.get("start_date")), field_path="medication.drug_name,review_status",
            )  # fmt: skip
        b.mark("medication_record", len(meds))


def read(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    profile = patients.patient_profile(db, user, request.subject_id, refresh=False)
    b.as_of = profile.as_of_date
    dump = json.loads(profile.model_dump_json())
    # The legacy projection (health withheld as a whole) for the core categories, and the
    # central policy over the whole record, which must also accept every field.
    model_view, withheld = legacy.split_patient_360(dump)
    # Validation only for the core categories: the Case Brief keeps exactly its historical
    # withheld and identifier lists (adding policy-wide strings would change its checks).
    b.decisions.update(project("patient_360", dump).decisions)
    core_items(b, profile, model_view, profile.as_of_date, int(get_config(db, "pdc_window_days")))
    if b.wanted & set(HEALTH):
        health_items(b, dump["health"], profile.as_of_date)
    notes = reader_notes(profile)
    b.notes += notes
    # Withheld text: the legacy list first (same order as before), then reader notes, then
    # anything the health categories or the vocabulary gate added.
    b.withheld[:0] = withheld + [n.text for n in notes]
    b.identifiers += identifiers(profile)
    vocab = {t.drug_name for t in profile.therapies if is_vocabulary_drug(t.drug_name)}
    vocab |= {
        m.get("drug_name")
        for m in profile.health.get("medications") or []
        if m.get("drug_name") and is_vocabulary_drug(m["drug_name"])
    }
    b.vocabulary |= vocab
    if patients.stale_therapies(db, user, request.subject_id):
        b.stale |= {"rx", "pdc"}


SOURCE_DEF = ContextSource(
    id=SOURCE,
    domain="Patient 360 (staff)",
    subject_types=(SubjectType.PATIENT,),
    categories=(
        Category("patient", "patient_360", "Age", terms=("age",)),
        Category(
            "rx",
            "therapy",
            "Confirmed therapies: supply, last refill, next due date",
            terms=("refill", "refills", "supply", "therapy", "therapies", "fill", "fills"),
        ),
        Category(
            "pdc",
            "therapy",
            "Proportion of days covered per therapy",
            terms=("days covered", "pdc", "adherence", "coverage"),
        ),
        Category(
            "touch",
            "interaction",
            "Latest outreach records (channel, type, outcome)",
            terms=("outreach", "contact", "contacts", "call", "calls", "message", "messages"),
        ),
        Category(
            "nba",
            "recommendation_summary",
            "Open recommendation: action, channel, status",
            terms=("recommendation", "recommendations", "next best action"),
        ),
        Category(
            "consent",
            "patient_360",
            "Outreach consent in effect",
            model=False,
            terms=("consent", "consents"),
        ),
        Category("absent", "patient_360", "Core facts verified as not recorded"),
        Category(
            "care_request",
            "care_request",
            "Care requests: type, status, due date",
            terms=("request", "requests", "consultation", "consultations"),
        ),
        Category(
            "condition",
            "condition",
            "Condition records: code and status",
            terms=("condition", "conditions", "diagnosis"),
        ),
        Category(
            "medication_record",
            "medication",
            "Medication records: name and review state",
            terms=("medication", "medications", "medicine", "medicines"),
        ),
    ),
    read=read,
    read_services=("services.patients.patient_profile", "services.patients.stale_therapies"),
)
