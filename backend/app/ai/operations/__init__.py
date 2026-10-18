"""Approved AI operations. Importing this package registers them (`app.ai.registry`).

Owner approvals: Case Brief (2026-10-16); decision trace (X1), engagement brief (M1), queue brief
(C1), compliance summary (R2) and cycle time (X2) (2026-10-19). No other operation is approved.
"""

from app.ai.operations import (  # noqa: F401
    case_brief,
    compliance_summary,
    cycle_time,
    decision_trace,
    engagement_brief,
    queue_brief,
)
