"""What every context source shares: its declaration and the builder it writes into.

A source reads one subject type (or several) through typed read services in `app.services`,
projects every record through the central field policy (`app.ai.policy`) and writes
model-visible facts as `ContextItem`s with provenance. It never queries ORM models or calls a
router, and it never decides authorisation: the read services do, and their `Forbidden` and
`NotFound` propagate unchanged.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy.orm import Session

from app.ai.contracts import ContextItem, ContextRequest, ReaderNote, SubjectType
from app.ai.policy import Projection
from app.core import pseudonym
from app.models import User


class SourceStatus(StrEnum):
    IMPLEMENTED = "implemented"  # registered and buildable
    DISABLED = "disabled"  # registered, switched off
    POLICY_BLOCKED = "policy_blocked"  # buildable for deterministic use; no model consumer
    UNSUPPORTED = "unsupported"  # inventoried only, with the reason


@dataclass(frozen=True)
class Category:
    """One kind of fact a source can supply.

    `record` is the field-policy record type the item text is written from. `model` is False
    for categories whose items the server cites in its own text but a model never reads (for
    example consent): such a category can never enter a model's view, whatever a consumer
    asks for. `terms` feed the absence check (`safety.check_absence`)."""

    name: str
    record: str
    description: str
    model: bool = True
    # Words a sentence uses when it talks about this category; the shared absence check maps
    # a "not recorded" claim to categories through them.
    terms: tuple[str, ...] = ()
    # How freshness is judged when a request sets `max_age_days`: "item" by the dates on the
    # category's items (no date means freshness unknown, so stale); "current" for facts that
    # describe the current state as read (counts, aggregates, profile attributes).
    freshness: str = "item"


class SourceUnavailable(RuntimeError):
    """The source cannot be read on this database (missing table, failed query)."""


@dataclass
class Builder:
    """Collects what a source read: items, reader notes, withheld text, identifiers, facts for
    deterministic text, and per-category bookkeeping for the engine's states."""

    request: ContextRequest
    source: str
    salt: str  # the namespace of this builder's opaque references (the source id)
    wanted: frozenset[str] = frozenset()  # categories the consumer asked for
    max_records: int = 20  # page size for collections (the request's limit)
    items: list[ContextItem] = field(default_factory=list)
    notes: list[ReaderNote] = field(default_factory=list)
    withheld: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    vocabulary: set[str] = field(default_factory=set)
    facts: dict = field(default_factory=dict)
    decisions: dict[str, str] = field(default_factory=dict)
    read: set[str] = field(default_factory=set)  # categories actually read
    totals: dict[str, int] = field(default_factory=dict)  # records the source holds
    stale: set[str] = field(default_factory=set)
    unavailable: set[str] = field(default_factory=set)
    # Categories cut by the page size or a sample limit: more records exist than were read.
    # A documented window (the latest five outreach records) is not truncation.
    truncated: set[str] = field(default_factory=set)
    as_of: date | None = None
    _counts: Counter = field(default_factory=Counter)

    def ref(self, raw_id: str | int | None) -> str | None:
        """Opaque reference for a record or subject: a keyed hash, never the id
        (`core.pseudonym`)."""
        return pseudonym.ref(self.salt, raw_id)

    def add(
        self,
        kind: str,
        text: str,
        source: str,
        source_id: str | int,
        recorded: date | datetime | None = None,
        *,
        category: str | None = None,
        field_path: str | None = None,
        version: str | int | None = None,
        as_of: date | None = None,
        subject: str | int | None = None,
    ) -> str:
        """Adds one item; returns its evidence id (`ctx:<kind>:<n>`, numbered per kind)."""
        self._counts[kind] += 1
        cid = f"ctx:{kind}:{self._counts[kind]}"
        extra = {}
        if category is not None and category != kind:
            extra["category"] = category
        if field_path is not None:
            extra["field"] = field_path
        if version is not None:
            extra["source_version"] = str(version)
        if as_of is not None:
            extra["as_of"] = as_of
        if subject is not None:
            extra["subject_ref"] = self.ref(subject)
        self.items.append(
            ContextItem(
                id=cid,
                kind=kind,
                text=text,
                source=source,
                source_id=str(source_id),
                recorded_at=recorded,
                **extra,
            )
        )
        return cid

    def absorb(self, projection: Projection, prefix: str = "") -> Projection:
        """Records what a projection withheld and identified, so the request checks can prove
        none of it reaches a model."""
        self.withheld += projection.withheld
        self.identifiers += projection.identifiers
        self.decisions.update({f"{prefix}{k}": v for k, v in projection.decisions.items()})
        return projection

    def mark(self, category: str, total: int | None = None) -> None:
        self.read.add(category)
        if total is not None:
            self.totals[category] = total


Reader = Callable[[Session, User, ContextRequest, Builder], None]


@dataclass(frozen=True)
class ContextSource:
    """A registered context source.

    - `subject_types`: what it can be asked about.
    - `categories`: what it can supply, each tied to a field-policy record.
    - `read`: the function that reads through the typed services into a `Builder`.
    - `read_services`: the service functions it relies on (documentation and diagnostics).
    - `principal_dependent`: the view differs by reader (a queue, an inbox, scoped counts), so
      the reader's scope is part of the fingerprint.
    - `status` / `status_reason`: see `SourceStatus`.
    """

    id: str
    domain: str
    subject_types: tuple[SubjectType, ...]
    categories: tuple[Category, ...]
    read: Reader | None
    read_services: tuple[str, ...]
    principal_dependent: bool = False
    status: SourceStatus = SourceStatus.IMPLEMENTED
    status_reason: str = ""

    def category(self, name: str) -> Category | None:
        return next((c for c in self.categories if c.name == name), None)

    @property
    def category_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.categories)
