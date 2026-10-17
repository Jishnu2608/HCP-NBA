"""Bounded multi-subject contexts: several records of one subject type, each authorised on its
own, combined without letting one unauthorised record contaminate the rest.

    build_many(db, user, subject_type, subject_ids, consumer)

- At most `MAX_SUBJECTS` ids; duplicates are removed, order kept.
- Each id is built through the shared engine with the same consumer, so the read services
  scope every record before anything is combined. An id the reader may not see (or that does
  not exist) is counted as `out_of_scope`, never named, and contributes nothing.
- A record whose source failed is counted as `failed`.
- `complete` is False when any requested id was out of scope, failed, or produced an incomplete
  context, or when ids beyond the limit were dropped: partial results never pass as complete.
- Members are listed by ordinal and opaque reference, never by id.

Collections the application already defines (a care manager's queue, an HCP's consultations)
are single subject types with their own paging (`SubjectType.CARE_QUEUE`, ...), and aggregates
(`SubjectType.CARE_WORKFLOW`, ...) are computed in code by their sources.
"""

import hashlib
import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.ai.contracts import ID_SUBJECTS, AuthorizedContext, ContextRequest, SubjectType
from app.ai.engine import Consumer, build_context, scope_key
from app.ai.sources import SourceUnavailable
from app.core import pseudonym
from app.models import User
from app.services.errors import Forbidden, NoRecord, NotFound

MAX_SUBJECTS = 20


@dataclass
class Member:
    ordinal: int
    ref: str
    context: AuthorizedContext


@dataclass
class MultiContext:
    subject_type: SubjectType
    members: list[Member] = field(default_factory=list)
    requested: int = 0
    out_of_scope: int = 0  # missing or outside the reader's scope (indistinguishable)
    failed: int = 0  # the source could not be read
    dropped: int = 0  # ids beyond MAX_SUBJECTS
    fingerprint: str = ""

    @property
    def complete(self) -> bool:
        return (
            not self.out_of_scope
            and not self.failed
            and not self.dropped
            and all(m.context.complete for m in self.members)
        )

    def summary(self) -> dict:
        """Counts only: safe to show or audit."""
        return {
            "subject_type": str(self.subject_type),
            "requested": self.requested,
            "included": len(self.members),
            "out_of_scope": self.out_of_scope,
            "failed": self.failed,
            "dropped": self.dropped,
            "complete": self.complete,
        }


def build_many(
    db: Session,
    user: User,
    subject_type: SubjectType,
    subject_ids: list[str],
    consumer: Consumer,
    *,
    operation: str | None = None,
) -> MultiContext:
    subject_type = SubjectType(subject_type)
    if subject_type not in ID_SUBJECTS:
        raise ValueError(f"{subject_type} is not a record subject; use its own collection")
    unique = list(dict.fromkeys(subject_ids))
    result = MultiContext(subject_type=subject_type, requested=len(unique))
    result.dropped = max(0, len(unique) - MAX_SUBJECTS)
    for sid in unique[:MAX_SUBJECTS]:
        request = ContextRequest(
            operation=operation or consumer.id, subject_type=subject_type, subject_id=sid
        )
        try:
            ctx = build_context(db, user, request, consumer)
        except (NotFound, NoRecord):
            result.out_of_scope += 1
            continue
        except Forbidden:
            # The reader's role cannot read this kind of record at all: nothing is combined.
            raise
        except SourceUnavailable:
            result.failed += 1
            continue
        result.members.append(
            Member(
                ordinal=len(result.members) + 1,
                ref=pseudonym.ref(f"subject:{subject_type}", sid) or "",
                context=ctx,
            )
        )
    blob = json.dumps(
        [scope_key(user), str(subject_type), [m.context.fingerprint for m in result.members],
         result.out_of_scope, result.failed, result.dropped]
    )  # fmt: skip
    result.fingerprint = hashlib.sha256(blob.encode()).hexdigest()
    return result
