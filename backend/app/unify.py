"""Identity resolution, demonstrated: synthetic source records matched to HCP profiles.

The field guide calls unification "the hardest, most valuable step". The engine's HCP profiles
are already unified (the generator creates them that way), so this module shows the step on
its own: two synthetic upstream feeds, a CRM account list and a pharmacy-claims prescriber
file, are derived deterministically from the synthetic HCP profiles with the imperfections
real feeds have (missing NPIs, "LAST, First" names, initials only, a different ZIP), plus
records that belong to nobody in the system. Each record is matched by fixed rules:

  R1  NPI equal                                   -> merged, confidence 1.00
  R2  surname + first initial + ZIP, one profile  -> merged, confidence 0.90
  R3  surname + state, one profile                -> held for review, confidence 0.60
  any rule with more than one profile             -> ambiguous, never merged
  no rule                                         -> unmatched

Nothing is written: profiles are never changed, created or merged by this module, so the
demonstration cannot corrupt or duplicate an identity. Invited (real) HCPs registered
directly and have no upstream records. Everything here is synthetic.
"""

import re
from dataclasses import dataclass, field
from random import Random

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Hcp

SOURCES = {
    "crm": "CRM account list",
    "claims": "Pharmacy claims prescriber file",
}
RULES = {
    "R1": ("NPI matches", 1.0, "merged"),
    "R2": ("Surname, first initial and ZIP match one profile", 0.9, "merged"),
    "R3": ("Only surname and state match one profile", 0.6, "review"),
}
STATUS_LABEL = {
    "merged": "Merged",
    "review": "Held for review",
    "ambiguous": "Ambiguous: more than one profile fits",
    "unmatched": "No matching profile",
}
UNKNOWN_SURNAMES = ("Okafor", "Lindqvist", "Marchetti", "Haddad", "Nakamura")


@dataclass
class SourceRecord:
    source: str
    key: str
    name: str
    npi: str | None
    state: str | None
    zip: str | None
    # Generator truth, used only by the tests to check the matcher, never by the matcher.
    truth: str | None = None


@dataclass
class Match:
    record: SourceRecord
    status: str
    hcp_id: str | None = None
    rule: str | None = None
    confidence: float | None = None
    candidates: list[str] = field(default_factory=list)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def parse_name(name: str) -> tuple[str, str | None]:
    """(surname, first initial) from "LAST, First", "Dr J. Last", "First Last" or "Dr Last"."""
    if "," in name:
        last, first = (part.strip() for part in name.split(",", 1))
        return _norm(last), (_norm(first)[:1] or None)
    words = [w for w in re.split(r"\s+", name.strip()) if w.lower().rstrip(".") != "dr"]
    if len(words) == 1:
        return _norm(words[0]), None
    return _norm(words[-1]), (_norm(words[0])[:1] or None)


def source_records(hcps: list[Hcp]) -> list[SourceRecord]:
    """The two synthetic feeds, the same every time for the same profiles."""
    out: list[SourceRecord] = []
    synthetic = sorted((h for h in hcps if h.origin == "synthetic"), key=lambda h: h.hcp_id)
    for n, h in enumerate(synthetic, start=1):
        rng = Random(f"unify:{h.hcp_id}")
        if rng.random() < 0.92:
            out.append(
                SourceRecord(
                    "crm",
                    f"CRM-{n:05d}",
                    f"{h.last_name.upper()}, {h.first_name}",
                    h.npi if rng.random() < 0.85 else None,
                    h.state,
                    h.zip,
                    h.hcp_id,
                )
            )
        roll = rng.random()
        if roll < 0.80:
            name, npi, zip_code = f"{h.first_name} {h.last_name}", h.npi, h.zip
        elif roll < 0.92:
            name, npi = f"Dr {h.first_name[0]}. {h.last_name}", None
            zip_code = h.zip
        else:
            # A referral-style record: surname and state only.
            name, npi, zip_code = f"Dr {h.last_name}", None, None
        out.append(SourceRecord("claims", f"RX-{n:05d}", name, npi, h.state, zip_code, h.hcp_id))
    # Prescribers the system does not know.
    rng = Random("unify:unknown")
    states = sorted({h.state for h in synthetic if h.state}) or ["TX"]
    for i, surname in enumerate(UNKNOWN_SURNAMES, start=1):
        out.append(
            SourceRecord(
                "claims",
                f"RX-9{i:04d}",
                f"Dr {rng.choice('ABCDEFGHJKLMNPRS')}. {surname}",
                f"9{rng.randrange(10**8, 10**9)}",
                rng.choice(states),
                None,
            )
        )
    return out


def match_all(records: list[SourceRecord], hcps: list[Hcp]) -> list[Match]:
    by_npi = {h.npi: h.hcp_id for h in hcps if h.npi}
    by_zip: dict[tuple, list[str]] = {}
    by_state: dict[tuple, list[str]] = {}
    for h in hcps:
        last, initial = _norm(h.last_name), _norm(h.first_name)[:1]
        if h.zip:
            by_zip.setdefault((last, initial, h.zip[:5]), []).append(h.hcp_id)
        if h.state:
            by_state.setdefault((last, h.state), []).append(h.hcp_id)
    out = []
    for r in records:
        if r.npi and r.npi in by_npi:
            out.append(_matched(r, "R1", [by_npi[r.npi]]))
            continue
        last, initial = parse_name(r.name)
        candidates = by_zip.get((last, initial, (r.zip or "")[:5]), []) if initial and r.zip else []
        if candidates:
            out.append(_matched(r, "R2", candidates))
            continue
        candidates = by_state.get((last, r.state), []) if r.state else []
        if candidates:
            out.append(_matched(r, "R3", candidates))
            continue
        out.append(Match(r, "unmatched"))
    return out


def _matched(r: SourceRecord, rule: str, candidates: list[str]) -> Match:
    if len(candidates) > 1:
        return Match(r, "ambiguous", None, rule, None, sorted(candidates))
    _, confidence, status = RULES[rule]
    return Match(r, status, candidates[0], rule, confidence, candidates)


def resolve(db: Session) -> tuple[list[Hcp], list[Match]]:
    hcps = list(db.scalars(select(Hcp)))
    return hcps, match_all(source_records(hcps), hcps)


def match_out(m: Match) -> dict:
    return {
        "source": m.record.source,
        "source_label": SOURCES[m.record.source],
        "source_key": m.record.key,
        "name_in_source": m.record.name,
        "npi_in_source": m.record.npi,
        "state": m.record.state,
        "zip": m.record.zip,
        "status": m.status,
        "status_label": STATUS_LABEL[m.status],
        "rule": m.rule,
        "rule_label": RULES[m.rule][0] if m.rule else None,
        "confidence": m.confidence,
        "hcp_id": m.hcp_id,
        "candidates": m.candidates,
    }


def summary(db: Session) -> dict:
    hcps, matches = resolve(db)
    counts: dict[str, dict[str, int]] = {s: dict.fromkeys(STATUS_LABEL, 0) for s in SOURCES}
    for m in matches:
        counts[m.record.source][m.status] += 1
    merged = {m.hcp_id for m in matches if m.status == "merged"}
    synthetic = [h for h in hcps if h.origin == "synthetic"]
    return {
        "sources": [
            {"source": s, "label": label, "counts": counts[s]} for s, label in SOURCES.items()
        ],
        "rules": [
            {"rule": r, "label": label, "confidence": c, "status": st}
            for r, (label, c, st) in RULES.items()
        ],
        "status_labels": STATUS_LABEL,
        "profiles": len(synthetic),
        "profiles_with_merged_sources": len(merged),
        "records": len(matches),
        "exceptions": [match_out(m) for m in matches if m.status != "merged"],
    }


def for_hcp(db: Session, hcp: Hcp) -> dict:
    """The upstream records merged into one profile, and any held for review that point at it."""
    if hcp.origin != "synthetic":
        return {"registered_directly": True, "merged": [], "review": []}
    _, matches = resolve(db)
    return {
        "registered_directly": False,
        "merged": [
            match_out(m) for m in matches if m.status == "merged" and m.hcp_id == hcp.hcp_id
        ],
        # Other profiles a record could belong to are counted, not named: the reader may not
        # be allowed to see them.
        "review": [
            {**match_out(m), "candidates": [], "other_candidates": len(m.candidates) - 1}
            for m in matches
            if m.status in ("review", "ambiguous") and hcp.hcp_id in m.candidates
        ],
    }
