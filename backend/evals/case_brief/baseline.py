"""Deterministic template brief: the fallback every model must justify itself against.

Built only from the structured case fields, so it cannot invent anything. It does not read the
free-text note (untrusted text), which is exactly the value a model has to add.
"""

from .cases import Case
from .schema import CaseBrief, KeyPoint


def template_brief(c: Case) -> CaseBrief:
    pts: list[KeyPoint] = [
        KeyPoint(
            text=f"Patient {c.patient_id}, age {c.age}, enrolled since {c.enrolled.isoformat()}.",
            citations=["ctx:patient:1"],
        )
    ]
    missing: list[str] = []
    if c.last_refill and c.drug:
        pts.append(
            KeyPoint(
                text=f"{c.drug}: last refill {c.last_refill.isoformat()}, next refill was due "
                f"{c.due.isoformat()} and is not yet filled.",
                citations=["ctx:rx:1"],
            )
        )
    else:
        missing.append("No refill or therapy record.")
    if c.alt_refill:
        pts.append(
            KeyPoint(
                text=f"Refill records conflict: last refill {c.last_refill.isoformat()} versus "
                f"patient-reported {c.alt_refill.isoformat()}.",
                citations=["ctx:rx:1", "ctx:rx:2"],
            )
        )
    if c.pdc is not None:
        pts.append(
            KeyPoint(
                text=f"Proportion of days covered over the last 180 days is {c.pdc:.2f}.",
                citations=["ctx:pdc:1"],
            )
        )
    else:
        missing.append("Proportion of days covered (adherence) is not recorded.")
    if c.consent is not None:
        k = c.consent
        flag = " The two consent records conflict on phone." if c.consent_conflict else ""
        yes = ", ".join(n for n in ("email", "sms", "phone") if k[n]) or "none"
        pts.append(
            KeyPoint(
                text=f"Consented channels: {yes}.{flag}",
                citations=["ctx:consent:1"] + (["ctx:consent:2"] if c.consent_conflict else []),
            )
        )
    else:
        missing.append("No consent record.")
    if c.touches:
        d, what, outcome = max(c.touches)
        pts.append(
            KeyPoint(
                text=f"Last outreach {d.isoformat()}: {what}; {outcome}.",
                citations=[f"ctx:touch:{c.touches.index(max(c.touches)) + 1}"],
            )
        )
    else:
        missing.append("No outreach recorded.")
    if c.content_id:
        pts.append(
            KeyPoint(
                text=f"Open recommendation: {c.action} using {c.content_id}.",
                citations=["ctx:nba:1"],
            )
        )
        focus = f"Review the open recommendation using {c.content_id}."
    else:
        missing.append("No open recommendation.")
        focus = "No open recommendation is recorded."

    return CaseBrief(
        summary=f"{c.patient_id}: " + " ".join(p.text for p in pts[1:3]),
        key_points=pts[:8],
        suggested_focus=focus,
        not_recorded=missing,
    )
