"""The shared context engine: one way to build an authorised, minimised context for any consumer
and any registered subject type.

    build_context(db, user, request, consumer)

1. checks the consumer's permission on the server (`core.permissions.can`);
2. resolves the registered source for the subject type, refusing unsupported or disabled
   sources and refusing policy-blocked ones for a consumer that calls a model;
3. reads through the source's typed read services under `readonly.guard` (no write can
   happen); `Forbidden`, `NotFound` and `NoRecord` from the read services propagate unchanged,
   so out of scope looks exactly like missing, as in the API;
4. lets every record pass the central field policy (an unclassified field raises);
5. gives every category a state (`CategoryState`): included, verified empty, not requested,
   excluded by policy or allowlist, unavailable, truncated or stale, so "not in this context"
   is never confused with "not recorded";
6. reduces the model's view to the consumer's allowlist, intersected with what the policy
   permits, within the consumer's item and character budget (cut categories are truncated);
7. fingerprints the facts (adding the reader's scope for views that depend on the reader).

Any doubt fails closed. The engine decides nothing about who may see what: the read services do.
"""

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai import context_policy as legacy_policy
from app.ai import readonly
from app.ai.contracts import (
    AuthorizedContext,
    CategoryState,
    CategoryStatus,
    ContextItem,
    ContextRequest,
    OperationSpec,
)
from app.ai.policy import POLICY_VERSION
from app.ai.sources import (
    Builder,
    ContextSource,
    SourceStatus,
    SourceUnavailable,
    UnknownSource,
    source_for,
)
from app.core import clock
from app.core.permissions import Permission, can, permissions_for
from app.models import User
from app.services.errors import Forbidden

# Global caps no consumer may exceed (checked at registration and applied here).
GLOBAL_MAX_ITEMS = 120
GLOBAL_MAX_CHARS = 20000
INCOMPLETE = frozenset({CategoryState.TRUNCATED, CategoryState.UNAVAILABLE, CategoryState.STALE})


class UnknownCategory(ValueError):
    """A request named a category its subject's source does not supply."""


class PolicyBlocked(Forbidden):
    """The source may be built for deterministic use only; no model consumer may read it."""


@dataclass(frozen=True)
class Consumer:
    """Who is asking for a context and what it may read.

    - `permission`: checked on the server before anything is read.
    - `categories`: the allowlist. The model view never holds anything outside it.
    - `model_calls`: False for consumers that never send a context to a model (diagnostics,
      deterministic views); only those may build policy-blocked sources.
    """

    id: str
    # None only for the diagnostics fixture world: no operation permission is assumed and the
    # read services alone decide what the synthetic principal may read. Never a model consumer.
    permission: str | None
    categories: tuple[str, ...]
    model_calls: bool = True
    max_items: int = 40
    max_chars: int = 6000


def consumer_for(spec: OperationSpec) -> Consumer:
    return Consumer(
        id=spec.id,
        permission=spec.permission,
        categories=tuple(spec.context_kinds),
        model_calls=True,
        max_items=spec.max_context_items,
        max_chars=spec.max_context_chars,
    )


def diagnostics_consumer(source: ContextSource) -> Consumer:
    """The Admin diagnostics consumer: every category of the source, never a model call."""
    return Consumer(
        id="diagnostics",
        permission=Permission.AI_DIAGNOSTICS,
        categories=source.category_names,
        model_calls=False,
        max_items=GLOBAL_MAX_ITEMS,
        max_chars=GLOBAL_MAX_CHARS,
    )


def scope_key(user: User) -> str:
    """The reader's scope: account, its role's permissions and the policy version. Two readers
    with different scopes never share a fingerprint for a principal-dependent view."""
    perms = sorted(p.value for p in permissions_for(user.role))
    blob = json.dumps([user.id, user.role, perms, POLICY_VERSION])
    return hashlib.sha256(blob.encode()).hexdigest()


def fingerprint(request: ContextRequest, as_of, items: list[ContextItem], scope: str = "") -> str:
    """The Case Brief's formula, unchanged, plus the reader's scope when the view depends on it."""
    payload: dict = {
        "op": request.operation,
        "subject": [request.subject_type, request.subject_id],
        "as_of": as_of.isoformat(),
        "items": [[i.id, i.kind, i.text] for i in items],
    }
    if scope:
        payload["scope"] = scope
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _category_of(item: ContextItem) -> str:
    return item.category or item.kind


def build_context(
    db: Session, user: User, request: ContextRequest, consumer: Consumer
) -> AuthorizedContext:
    if consumer.permission is None:
        if consumer.model_calls:
            raise Forbidden("A model consumer must name its permission.")
    elif not can(user, consumer.permission):
        raise Forbidden("Your account cannot use this operation.")
    try:
        source = source_for(request.subject_type)
    except UnknownSource:
        raise Forbidden("Unsupported subject.") from None
    if source.status in (SourceStatus.DISABLED, SourceStatus.UNSUPPORTED):
        raise SourceUnavailable(f"source_{source.status}")
    if source.status is SourceStatus.POLICY_BLOCKED and consumer.model_calls:
        raise PolicyBlocked("This subject may not be given to a model under the current policy.")

    known = set(source.category_names)
    requested = tuple(request.categories) or consumer.categories
    unknown = [c for c in requested if c not in known]
    if unknown:
        raise UnknownCategory(f"{source.id} has no categories {sorted(unknown)}")
    allowed = set(consumer.categories) & known
    wanted = frozenset(c for c in requested if c in allowed)

    b = Builder(request=request, source=source.id, salt=source.id, wanted=wanted,
                max_records=request.limit)  # fmt: skip
    failed = False
    with readonly.guard(db):
        try:
            source.read(db, user, request, b)
        except SourceUnavailable:
            failed = True
        except SQLAlchemyError:
            db.rollback()
            failed = True
    as_of = b.as_of or clock.get_today(db)

    # The model view: allowlisted, policy-eligible categories that were asked for.
    model_categories = {
        c.name for c in source.categories if c.model and c.name in wanted and not failed
    }
    eligible = [i for i in b.items if _category_of(i) in model_categories]
    legacy_policy.model_items(eligible, tuple({i.kind for i in eligible}))
    model_items: list[ContextItem] = []
    budget_cut: set[str] = set()
    chars = 0
    limit = min(consumer.max_items, GLOBAL_MAX_ITEMS)
    char_limit = min(consumer.max_chars, GLOBAL_MAX_CHARS)
    for item in eligible:
        if len(model_items) >= limit or chars + len(item.text) > char_limit:
            budget_cut.add(_category_of(item))
            continue
        model_items.append(item)
        chars += len(item.text)

    statuses: list[CategoryStatus] = []
    for c in source.categories:
        count = sum(1 for i in model_items if _category_of(i) == c.name)
        asked = c.name in requested
        in_allow = c.name in allowed
        total = b.totals.get(c.name)
        if failed or c.name in b.unavailable:
            state, reason = CategoryState.UNAVAILABLE, "source_unavailable"
        elif not c.model and (asked or in_allow):
            state, reason = CategoryState.EXCLUDED_POLICY, "server_text_only"
        elif asked and not in_allow:
            state, reason = CategoryState.EXCLUDED_ALLOWLIST, "outside_allowlist"
        elif not asked:
            if c.name not in b.read:
                state, reason = CategoryState.NOT_REQUESTED, None
            elif not c.model:
                state, reason = CategoryState.EXCLUDED_POLICY, "server_text_only"
            else:
                state, reason = CategoryState.EXCLUDED_ALLOWLIST, "outside_allowlist"
        elif c.name not in b.read:
            state, reason = CategoryState.UNAVAILABLE, "not_read"
        elif c.name in b.stale:
            state, reason = CategoryState.STALE, "figures_not_current"
        elif c.name in b.truncated:
            state, reason = CategoryState.TRUNCATED, "more_records_exist"
        elif c.name in budget_cut:
            state, reason = CategoryState.TRUNCATED, "context_budget"
        elif count:
            # A documented window (the latest five outreach records) is complete for what it
            # promises, but says so: the source holds more than the window shows.
            window = total is not None and total > count and c.name not in b.truncated
            state, reason = CategoryState.INCLUDED, ("window" if window else None)
        else:
            state, reason = CategoryState.VERIFIED_EMPTY, None
        statuses.append(
            CategoryStatus(category=c.name, state=state, count=count, total=total, reason=reason)
        )
    complete = not any(s.state in INCOMPLETE for s in statuses if s.category in wanted)

    scope = scope_key(user) if source.principal_dependent else ""
    return AuthorizedContext(
        request=request,
        as_of=as_of,
        items=b.items,
        model_items=model_items,
        reader_notes=b.notes,
        facts=b.facts,
        withheld_texts=b.withheld,
        identifiers=sorted({v for v in b.identifiers if v}),
        vocabulary=sorted(b.vocabulary),
        fingerprint=fingerprint(request, as_of, b.items, scope),
        categories=statuses,
        complete=complete,
        policy_version=POLICY_VERSION,
        source=source.id,
        scope_key=scope,
    )


__all__ = [
    "GLOBAL_MAX_CHARS",
    "GLOBAL_MAX_ITEMS",
    "Consumer",
    "PolicyBlocked",
    "UnknownCategory",
    "build_context",
    "consumer_for",
    "diagnostics_consumer",
    "fingerprint",
    "scope_key",
]
