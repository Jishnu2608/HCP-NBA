"""Registered AI operations. An operation exists only if it is registered here.

Registration is explicit and happens at import of `app.ai.operations`. The registry holds only
approved operations; it is the extension point for future ones, each supplying its own
`OperationSpec` (identifier, version, permission, context allowlist, prompt, output schema,
checks and fallback). It decides nothing about which operations should exist.
"""

from app.ai.contracts import OperationSpec


class UnknownOperation(LookupError):
    pass


_REGISTRY: dict[str, OperationSpec] = {}


def register(spec: OperationSpec) -> OperationSpec:
    if spec.id in _REGISTRY and _REGISTRY[spec.id] is not spec:
        raise ValueError(f"operation {spec.id!r} is already registered")
    if spec.allow_cloud:
        raise ValueError("cloud inference is disabled: no operation may opt in")
    if not spec.context_kinds:
        raise ValueError("an operation must declare its context allowlist")
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
