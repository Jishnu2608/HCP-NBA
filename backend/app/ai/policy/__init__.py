"""The application-wide context policy: field classification and its enforcement."""

from app.ai.policy.engine import Projection, is_vocabulary_drug, project
from app.ai.policy.fields import (
    FIELD_POLICY,
    GLOBAL_PROHIBITED,
    MODEL_VISIBLE,
    POLICY_VERSION,
    Disposition,
    PolicyError,
    model_visible_fields,
    validate_policy,
)

__all__ = [
    "FIELD_POLICY",
    "GLOBAL_PROHIBITED",
    "MODEL_VISIBLE",
    "POLICY_VERSION",
    "Disposition",
    "PolicyError",
    "Projection",
    "is_vocabulary_drug",
    "model_visible_fields",
    "project",
    "validate_policy",
]

from app.ai.policy import approvals  # noqa: E402

approvals.validate()
