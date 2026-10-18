"""Owner approvals for policy-blocked AI audiences and pending-review data (F-14).

One register decides what is open. It is empty: every entry is None, so everything below stays
blocked and fails closed (owner decisions 2026-10-17 and 2026-10-19). Opening anything later is
a reviewed change to this file, recording the decision, not a rework of the layer:

- **Audiences.** `patient_facing` (the patient's own record, `patient_self`) and `hcp_facing`
  (an HCP's own consultations, `consultation_queue`). A source serving an unapproved audience is
  POLICY_BLOCKED: registration refuses any operation on it and the engine refuses it to every
  model consumer. With an approval, an operation on it must still declare a consent purpose
  and an evaluation reference (registration), and for `patient_facing` the patient's consent
  for that purpose must be in effect when the generation runs. Consent alone never enables an
  audience; the approval alone never skips the consent. No approval permits diagnosis,
  prescribing or clinical decisions: the shared output checks stay in force.
- **Pending-review data groups.** Every PENDING_REVIEW field belongs to exactly one group
  (`PENDING_GROUP`, checked at import). A field of an approved group projects as model-visible;
  a globally prohibited field name still needs the approval to name it in
  `overrides_prohibited`. A source must also choose to write the field into its item text,
  which is a reviewed source change.

Approval for MLR distribution is not approval for AI processing; neither is a role, an Admin
permission or an allowlist entry.
"""

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Approval:
    decision: str  # where the owner's decision is recorded
    approved_on: date
    approved_by: str
    scope: str  # what exactly is approved
    evaluation: str  # evidence that safety, privacy and output-validation tests passed
    overrides_prohibited: tuple[str, ...] = field(default=())


AUDIENCES: dict[str, Approval | None] = {
    # Owner 2026-10-19: blocked until explicit policy approval, valid informed consent,
    # patient-specific access controls and passed safety, privacy and output tests.
    "patient_facing": None,
    # Owner 2026-10-19: blocked until a separate policy, privacy and safety review.
    "hcp_facing": None,
}

FIELD_GROUPS: dict[str, Approval | None] = {
    # Owner 2026-10-19: titles, body text and claims stay out of model inputs.
    "mlr_wording": None,
    # Owner 2026-10-19: segment, value score, prescribing volume stay deterministic only.
    "hcp_commercial": None,
    # Owner 2026-10-19: group adherence and quality-measure figures stay deterministic only.
    "group_adherence": None,
}

# Which audience each audience-specific source serves.
SOURCE_AUDIENCE = {"patient_self": "patient_facing", "consultation_queue": "hcp_facing"}
# Audiences whose generations need the subject's own consent, checked when they run.
CONSENT_AUDIENCES = frozenset({"patient_facing"})

PENDING_GROUP: dict[tuple[str, str], str] = {
    ("hcp_360", "rx_volume_annual"): "hcp_commercial",
    ("hcp_360", "segment"): "hcp_commercial",
    ("hcp_360", "value_score"): "hcp_commercial",
    ("hcp_360", "panel_adherence"): "group_adherence",
    ("task_content", "title"): "mlr_wording",
    ("recommendation", "content_title"): "mlr_wording",
    ("candidate", "content_title"): "mlr_wording",
    ("content", "title"): "mlr_wording",
    ("content", "body"): "mlr_wording",
    ("recommendation_content", "title"): "mlr_wording",
    ("recommendation_content", "body"): "mlr_wording",
    ("quality_measure", "patients"): "group_adherence",
    ("quality_measure", "adherent_share"): "group_adherence",
    ("quality_measure", "persistence_share"): "group_adherence",
    ("quality_measure", "courses"): "group_adherence",
}


class ApprovalError(ValueError):
    pass


def audience_of(source_id: str) -> str | None:
    return SOURCE_AUDIENCE.get(source_id)


def audience_approval(audience: str | None) -> Approval | None:
    return AUDIENCES.get(audience) if audience else None


def field_approved(record: str, name: str) -> bool:
    """True when the pending-review field's group is approved for model use (and, for a
    globally prohibited name, the approval names it)."""
    from app.ai.policy.fields import GLOBAL_PROHIBITED

    group = PENDING_GROUP.get((record, name))
    approval = FIELD_GROUPS.get(group) if group else None
    if approval is None:
        return False
    return name not in GLOBAL_PROHIBITED or name in approval.overrides_prohibited


def validate(policy=None) -> None:
    """Every PENDING_REVIEW field belongs to a known group, every group and audience is known,
    and every approval records its decision, approver, scope and evaluation evidence."""
    from app.ai.policy.fields import FIELD_POLICY, Disposition

    policy = policy or FIELD_POLICY
    pending = {
        (record, name)
        for record, fields in policy.items()
        for name, rule in fields.items()
        if rule == Disposition.PENDING_REVIEW
    }
    if pending != set(PENDING_GROUP):
        raise ApprovalError(
            f"pending fields and groups differ: {sorted(pending ^ set(PENDING_GROUP))}"
        )
    if set(PENDING_GROUP.values()) - set(FIELD_GROUPS):
        raise ApprovalError("a pending field names an unknown group")
    if (set(SOURCE_AUDIENCE.values()) | CONSENT_AUDIENCES) - set(AUDIENCES):
        raise ApprovalError("an audience is not in the register")
    for approval in (*AUDIENCES.values(), *FIELD_GROUPS.values()):
        if approval is not None and not all(
            (approval.decision, approval.approved_by, approval.scope, approval.evaluation)
        ):
            raise ApprovalError(
                "an approval must record its decision, approver, scope and evaluation"
            )
