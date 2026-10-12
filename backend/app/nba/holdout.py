"""Randomised holdout for measuring the engine's effect, on synthetic patients only.

When the `holdout` setting is on, a fixed share of synthetic patients receives no engine
outreach. Membership is decided from the patient id alone (a hash, not a random draw at run
time), so it is the same in every cycle, reproducible and auditable, and independent of
anything the engine knows about the patient. Real patients (self-registered or clinic) are
never held out: withholding outreach from a real person is not an experiment this system runs.

The comparison (`analytics/outcomes.holdout_comparison`) reports what happened to each group.
On synthetic data the outcomes come from the simulator's behaviour model, so a difference is
an estimate of the effect inside the simulation, not evidence of real-world lift.
"""

import hashlib

from app.models import Patient
from app.models.enums import PatientOrigin

SALT = "holdout-v1"


def bucket(patient_id: str) -> float:
    """A fixed number in [0, 1) for this patient."""
    digest = hashlib.sha256(f"{SALT}:{patient_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def eligible(patient: Patient) -> bool:
    return patient.origin == PatientOrigin.SYNTHETIC


def in_holdout(patient: Patient, config: dict) -> bool:
    return (
        bool(config.get("enabled"))
        and eligible(patient)
        and bucket(patient.patient_id) < config["share"]
    )


def assigned(patient: Patient, config: dict) -> bool:
    """Group membership for the comparison, whether or not the holdout is on right now."""
    return eligible(patient) and bucket(patient.patient_id) < config["share"]
