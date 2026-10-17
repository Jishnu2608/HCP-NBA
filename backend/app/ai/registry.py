"""Registered AI operations. An operation exists only if it is registered here.

Registration is explicit and happens at import of `app.ai.operations`. The registry holds only
approved operations; it is the extension point for future ones, each supplying its own
`OperationSpec` (identifier, version, permission, subject type, context allowlist, prompt,
output schema, checks and fallback). It decides nothing about which operations should exist,
and registration grants nothing: every request is still authorised, scoped and checked by the
context engine and the shared safety checks.

Refused at registration:

- an operation that opts into cloud inference;
- an operation without a context allowlist, or with a category its subject's source does not
  supply, or a category the field policy keeps from a model (server-text-only categories);
- an operation on a subject type with no registered source, or on a policy-blocked source
  (the patient's own record: patient-facing AI wording is not decided);
- an operation whose limits exceed the global caps;
- an operation whose permission is unknown, or is the diagnostics permission (diagnostics never
  generate);
- an operation whose output schema holds text the shared safety checks would not cover
  (`app.ai.output_contract`): every string must be cited text, uncited text or a Literal /
  Enum value, so no consumer can add an unchecked text field;
- a test-only specification (tag `test_only`): the evaluation harness validates those with
  `validate` but they are never registered, so they can never run in the application.
"""

import re

from app.ai.contracts import OperationSpec, SubjectType
from app.ai.output_contract import OutputContractError, classify


class UnknownOperation(LookupError):
    pass


_REGISTRY: dict[str, OperationSpec] = {}

MAX_OUTPUT_TOKENS = 4000
MAX_OUTPUT_CHARS = 8000


def validate(spec: OperationSpec) -> dict[str, str]:
    """Refuses an unsafe or incomplete specification (`ValueError`). Returns the classification
    of every string path of its output (`output_contract.classify`)."""
    from app.ai.engine import GLOBAL_MAX_CHARS, GLOBAL_MAX_ITEMS
    from app.ai.sources import SourceStatus, UnknownSource, source_for
    from app.core.permissions import Permission

    if not re.fullmatch(r"[a-z][a-z_]{0,47}", spec.id or ""):
        raise ValueError("an operation id is a lower-case code")
    if spec.allow_cloud:
        raise ValueError("cloud inference is disabled: no operation may opt in")
    try:
        permission = Permission(spec.permission)
    except ValueError:
        raise ValueError(f"unknown permission {spec.permission!r}") from None
    if permission is Permission.AI_DIAGNOSTICS:
        raise ValueError("the diagnostics permission never authorises a model operation")
    if spec.mode_setting is not None:
        from app.core.config import Settings

        if not (spec.mode_setting.startswith("ai_") and spec.mode_setting.endswith("_mode")) or (
            spec.mode_setting not in Settings.model_fields
        ):
            raise ValueError("mode_setting must name an existing ai_*_mode setting")
    if not spec.context_kinds:
        raise ValueError("an operation must declare its context allowlist")
    if len(set(spec.context_kinds)) != len(spec.context_kinds):
        raise ValueError("duplicate categories in the context allowlist")
    try:
        subject = SubjectType(spec.subject_type)
        source = source_for(subject)
    except (ValueError, UnknownSource):
        raise ValueError(f"no context source for subject type {spec.subject_type!r}") from None
    if source.status is not SourceStatus.IMPLEMENTED:
        raise ValueError(f"{source.id} is {source.status}: no model operation may use it")
    for kind in spec.context_kinds:
        category = source.category(kind)
        if category is None:
            raise ValueError(f"{source.id} supplies no category {kind!r}")
        if not category.model:
            raise ValueError(f"{source.id}.{kind} is server text only, never model context")
    if spec.max_context_items > GLOBAL_MAX_ITEMS or spec.max_context_chars > GLOBAL_MAX_CHARS:
        raise ValueError("context budget above the global caps")
    if spec.max_output_tokens > MAX_OUTPUT_TOKENS or spec.max_output_chars > MAX_OUTPUT_CHARS:
        raise ValueError("output limits above the global caps")
    if spec.max_context_items < 1 or spec.max_context_chars < 1 or spec.max_output_chars < 1:
        raise ValueError("limits must be positive")
    if spec.max_age_days is not None and not 0 <= spec.max_age_days <= 3650:
        raise ValueError("max_age_days must be between 0 and 3650")
    if not 0.0 <= spec.temperature <= 2.0:
        raise ValueError("temperature must be between 0 and 2")
    try:
        return classify(spec.output_model, tuple(spec.cited_fields), tuple(spec.text_fields))
    except OutputContractError as exc:
        raise ValueError(f"output contract: {exc}") from None


def register(spec: OperationSpec) -> OperationSpec:
    if spec.tags.get("test_only"):
        raise ValueError("a test-only specification is never registered")
    if spec.id in _REGISTRY and _REGISTRY[spec.id] is not spec:
        raise ValueError(f"operation {spec.id!r} is already registered")
    validate(spec)
    _REGISTRY[spec.id] = spec
    return spec


def resolve(operation: str) -> OperationSpec:
    from app.ai import operations  # noqa: F401 - registers the approved operations

    try:
        return _REGISTRY[operation]
    except KeyError:
        raise UnknownOperation(operation) from None


def registered() -> tuple[str, ...]:
    from app.ai import operations  # noqa: F401

    return tuple(sorted(_REGISTRY))
