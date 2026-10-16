"""What may reach a model, and what only a person may read.

Rule: a model never receives free text. Notes, comments, reasons typed by people and any other
narrative field are shown to an authorised reader word for word, labelled as untrusted, and are
never placed in a model prompt by any route (prompt text, serialised records, tool output,
retries or logs). A model may receive only allowlisted, structured fields.

Every field of the Patient 360 contract is classified here, so a new field cannot reach a model
until someone decides where it belongs (enforced by `tests/test_ai_context_policy.py`).
Authorisation, consent and scope are decided before this layer, by the read services.
"""

import json
import re
from enum import StrEnum

from app.ai.contracts import ContextItem, GenerationRequest, Sensitivity


class Use(StrEnum):
    MODEL = "model"  # structured, allowlisted: may be summarised by a model
    READER_ONLY = "reader_only"  # free text: shown verbatim to an authorised reader, never a model
    EXCLUDED = "excluded"  # identifiers or not needed: neither (identity is attached by the server)


# Patient 360 (`app.services.schemas.PatientProfile`), top-level fields.
PATIENT_360: dict[str, Use] = {
    "patient_id": Use.EXCLUDED,
    "name": Use.EXCLUDED,
    "age": Use.MODEL,
    "sex": Use.EXCLUDED,
    "plan_type": Use.EXCLUDED,
    "origin": Use.EXCLUDED,
    "city": Use.EXCLUDED,
    "state": Use.EXCLUDED,
    "country": Use.EXCLUDED,
    "region": Use.EXCLUDED,
    "location": Use.EXCLUDED,
    "preferred_channel": Use.MODEL,
    "risk_segment": Use.MODEL,
    "risk_history": Use.EXCLUDED,
    "as_of_date": Use.MODEL,
    "therapies": Use.MODEL,
    "fills": Use.MODEL,
    "care_team": Use.EXCLUDED,
    # Consent is stated by the server's own deterministic text, never summarised by a model.
    "consents": Use.EXCLUDED,
    "outreach_channels_granted": Use.EXCLUDED,
    "outreach_channels_total": Use.EXCLUDED,
    "care_managers": Use.EXCLUDED,
    "last_cycle_at": Use.EXCLUDED,
    "features": Use.EXCLUDED,
    "interactions": Use.MODEL,  # structured fields only, see INTERACTION
    "open_nba": Use.MODEL,
    # Conditions, reported medicines, care requests and care notes: typed by people.
    "health": Use.READER_ONLY,
}

# One interaction (`InteractionOut`): the typed note and the free-text reason never go to a model.
INTERACTION: dict[str, Use] = {
    "id": Use.EXCLUDED,
    "ts": Use.MODEL,
    "channel": Use.MODEL,
    "channel_label": Use.MODEL,
    "type": Use.MODEL,
    "type_label": Use.MODEL,
    "outcome": Use.MODEL,
    "outcome_ts": Use.MODEL,
    "content_id": Use.MODEL,
    "content_title": Use.MODEL,
    "content_version": Use.MODEL,
    "product": Use.MODEL,
    "source": Use.MODEL,
    "nba_id": Use.EXCLUDED,
    "intent": Use.MODEL,
    "outcome_reason": Use.READER_ONLY,
    "note": Use.READER_ONLY,
    "sent_by": Use.EXCLUDED,
}


# The one label every free-text note carries when shown to an authorised reader. Nothing about
# the note's wording changes it: no keyword detector or phrase list decides how a note is treated.
UNTRUSTED_NOTE_LABEL = "Untrusted; review for clinical content."


def label_notes(texts: list[str]) -> list[dict]:
    """Notes for the reader: each verbatim, each with the same label, in the order given."""
    return [{"label": UNTRUSTED_NOTE_LABEL, "text": t} for t in texts]


class UntrustedContentError(ValueError):
    """A free-text or personal item, or note text, was about to reach a model."""


def model_items(items: list[ContextItem], allowed_kinds: tuple[str, ...]) -> list[ContextItem]:
    """The items a model may read. Refuses (loudly) rather than silently dropping, so a context
    builder bug cannot hide: free text and personal data must be split off before this call."""
    for item in items:
        if item.sensitivity is not Sensitivity.OPERATIONAL:
            raise UntrustedContentError(f"{item.id} is {item.sensitivity}, not for a model")
        if item.kind not in allowed_kinds:
            raise UntrustedContentError(f"{item.id}: kind {item.kind!r} is not allowlisted")
    return list(items)


def reader_notes(items: list[ContextItem]) -> list[ContextItem]:
    """Free-text items for the authorised reader, unchanged and in order."""
    return [i for i in items if i.sensitivity is Sensitivity.FREE_TEXT]


def _strings(value) -> list[str]:
    """Narrative strings (three or more words, 20+ characters). Codes, ids, dates and single
    terms such as a drug name are structured values that may also appear in allowed fields."""
    if isinstance(value, str):
        return [value] if len(value) >= 20 and len(value.split()) >= 3 else []
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def split_patient_360(profile: dict) -> tuple[dict, list[str]]:
    """(fields a model may read, narrative texts withheld from it) from a Patient 360 dump that
    the read service already authorised. The withheld texts feed `assert_no_free_text`; the
    reader sees the full record from the read service itself. An unclassified field is refused,
    never passed through."""
    model: dict = {}
    reader: list[str] = []
    for key, value in profile.items():
        use = PATIENT_360.get(key)
        if use is None:
            raise UntrustedContentError(f"Patient 360 field {key!r} is not classified")
        if use is Use.READER_ONLY:
            reader += _strings(value)
        elif use is Use.MODEL and key == "interactions":
            rows = []
            for row in value or []:
                unknown = set(row) - set(INTERACTION)
                if unknown:
                    raise UntrustedContentError(f"interaction fields not classified: {unknown}")
                rows.append({k: v for k, v in row.items() if INTERACTION[k] is Use.MODEL})
                reader += [
                    s
                    for k, u in INTERACTION.items()
                    if u is Use.READER_ONLY and row.get(k)
                    for s in _strings(row[k])
                ]
            model[key] = rows
        elif use is Use.MODEL:
            model[key] = value
    return model, reader


def _fragments(text: str, size: int = 24) -> set[str]:
    words = re.sub(r"\s+", " ", text.strip().lower())
    if len(words) <= size:
        return {words} if len(words) >= 8 else set()
    return {words[i : i + size] for i in range(0, len(words) - size + 1, size // 2)}


def assert_no_free_text(request: GenerationRequest, withheld: list[str]) -> None:
    """Last check before a provider call: no withheld text appears anywhere in the request,
    including inside serialised JSON. Catches copying, not paraphrase (the model never had the
    text to paraphrase)."""
    blob = re.sub(r"\s+", " ", json.dumps(request.model_dump(), ensure_ascii=False).lower())
    blob += " " + re.sub(r"\s+", " ", " ".join(m.content.lower() for m in request.messages))
    for text in withheld:
        if any(f in blob for f in _fragments(text)):
            raise UntrustedContentError("withheld free text found in a model request")
