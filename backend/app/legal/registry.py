"""Versioned legal documents and consent statements.

A document version is a file `documents/<kind>-<version>.json` that is never edited once
published; a change is a new version. `VERSIONS` lists them oldest first. When a new
version sets `requires_reacceptance`, everyone who accepted an earlier one is asked again
(the server refuses other requests until they do: see legal/consents.py).

Placeholders written as {{name}} are filled from settings; unset values read
"[To be confirmed]" and mark the document as a draft.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.auth.errors import AuthError
from app.core.config import get_settings

DOCUMENTS_DIR = Path(__file__).parent / "documents"

# Oldest first. Add a version here and a file in documents/ to publish a change.
VERSIONS: dict[str, list[str]] = {
    "privacy": ["1.0"],
    "terms": ["1.0"],
    "cookies": ["1.0"],
}

TO_BE_CONFIRMED = "[To be confirmed]"

# Consent kinds that are recorded. Each points either at a document (the version accepted
# is that document's) or at a short consent statement with its own version.
CONSENT_KINDS: dict[str, dict] = {
    "terms": {"document": "terms", "label": "Terms & Conditions"},
    "privacy_ack": {"document": "privacy", "label": "Privacy Policy acknowledgement"},
    "health_data": {
        "version": "1.0",
        "label": "Processing of my health information",
        "statement": (
            "I explicitly consent to the processing of my health information (medications, "
            "prescription fills, adherence, and the outreach and recommendations based on "
            "them) to provide adherence support, as described in the Privacy Policy. I can "
            "withdraw this consent at any time in Data & privacy; withdrawal does not affect "
            "processing that took place before it."
        ),
        "withdrawable": True,
    },
}


def _placeholders() -> dict[str, str | None]:
    s = get_settings()
    days = s.privacy_response_days
    return {
        "controller_name": s.legal_controller_name,
        "controller_address": s.legal_controller_address,
        "privacy_email": s.legal_privacy_email,
        "dpo_contact": s.legal_dpo_contact,
        "eu_representative": s.legal_eu_representative,
        "governing_law": s.legal_governing_law,
        "hosting_region": s.legal_hosting_region,
        "response_days": str(days) if days else None,
    }


@lru_cache
def _raw(kind: str, version: str) -> str:
    return (DOCUMENTS_DIR / f"{kind}-{version}.json").read_text(encoding="utf-8")


def current_version(kind: str) -> str:
    return VERSIONS[kind][-1]


def version_index(kind: str, version: str) -> int:
    return VERSIONS[kind].index(version)


def minimum_accepted_version(kind: str) -> str:
    """The oldest version an acceptance may be for and still count: the latest version that
    required everyone to accept again (or the first version)."""
    versions = VERSIONS[kind]
    minimum = versions[0]
    for version in versions:
        if load(kind, version, fill=False)["requires_reacceptance"]:
            minimum = version
    return minimum


def load(kind: str, version: str | None = None, *, fill: bool = True) -> dict:
    if kind not in VERSIONS:
        raise AuthError(404, "document_not_found", "Document not found.")
    version = version or current_version(kind)
    if version not in VERSIONS[kind]:
        raise AuthError(404, "document_not_found", "Document version not found.")
    text = _raw(kind, version)
    missing: list[str] = []
    if fill:
        values = _placeholders()

        def substitute(match: re.Match) -> str:
            value = values.get(match.group(1))
            if not value:
                missing.append(match.group(1))
                return TO_BE_CONFIRMED
            return json.dumps(value)[1:-1]  # escaped for inside a JSON string

        text = re.sub(r"\{\{(\w+)\}\}", substitute, text)
    document = json.loads(text)
    document.update(
        kind=kind,
        version=version,
        current_version=current_version(kind),
        is_current=version == current_version(kind),
        draft=bool(missing) or document.get("draft", True),
        unconfirmed=sorted(set(missing)),
    )
    return document


def summary() -> list[dict]:
    out = []
    for kind in VERSIONS:
        doc = load(kind)
        out.append(
            {
                "kind": kind,
                "title": doc["title"],
                "version": doc["version"],
                "effective_date": doc["effective_date"],
                "last_updated": doc["last_updated"],
                "draft": doc["draft"],
            }
        )
    return out


def consent_version(kind: str) -> str:
    spec = CONSENT_KINDS[kind]
    return current_version(spec["document"]) if "document" in spec else spec["version"]
