"""Which parts of a model's output must pass the shared safety checks, derived from the output
schema itself rather than from what an operation chooses to list.

Every string an output model can hold is classified at registration:

- **constrained**: a `Literal` or `Enum` value. The schema fixes the possible values, so there
  is no free text to check.
- **citation**: a list of strings named as the citation field of a text field. Each entry must
  be an evidence id the model was given (`invalid_citation` otherwise).
- **cited text**: a text field paired with its citation field (`OperationSpec.cited_fields`).
  It makes factual claims; every fact it states must sit in the items it cites.
- **uncited text**: a text field declared in `OperationSpec.text_fields`. It is checked by every
  shared check with no citations, so any record fact it states counts as uncited and fails.

Any other string, a type the walker does not understand (`Any`, `dict`, unions of models, ...)
or a declared path that does not exist refuses the registration (`OutputContractError`). After
parsing, `text_leaves` walks the actual output and returns every string with its classification,
and `evaluate` refuses an output holding a string no rule covers. Paths use dots for nested
models and `[]` for list elements, for example `points[].text`.
"""

import enum
import types
import typing
from dataclasses import dataclass
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel


class OutputContractError(ValueError):
    """The output schema holds text the shared checks would not cover, or cannot be analysed."""


@dataclass(frozen=True)
class OutputField:
    path: str  # e.g. "summary", "points[].text"
    kind: str  # "text", "citation" or "constrained"


def _unwrap_optional(tp):
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0]
        raise OutputContractError("union output types are not supported")
    return tp


def _annotated_base(tp):
    if get_origin(tp) is typing.Annotated:
        return get_args(tp)[0]
    return tp


def _is_constrained(tp) -> bool:
    if get_origin(tp) is Literal:
        return True
    return isinstance(tp, type) and issubclass(tp, enum.Enum)


def schema_fields(model: type[BaseModel], prefix: str = "") -> list[OutputField]:
    """Every string-bearing path of an output model, classified as text or constrained. Numbers
    and booleans are not text. Citation fields are re-classified by `classify`."""
    out: list[OutputField] = []
    for name, info in model.model_fields.items():
        path = f"{prefix}{name}"
        out += _walk(info.annotation, path)
    return out


def _walk(tp, path: str) -> list[OutputField]:
    tp = _annotated_base(_unwrap_optional(_annotated_base(tp)))
    if tp is Any or tp is object:
        raise OutputContractError(f"{path}: untyped output field")
    if _is_constrained(tp):
        return [OutputField(path, "constrained")]
    if tp is str:
        return [OutputField(path, "text")]
    if tp in (int, float, bool):
        return []
    origin = get_origin(tp)
    if origin in (list, tuple, set, frozenset):
        args = [a for a in get_args(tp) if a is not Ellipsis]
        if len(args) != 1:
            raise OutputContractError(f"{path}: heterogeneous sequences are not supported")
        return _walk(args[0], f"{path}[]")
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        if tp.model_config.get("extra") != "forbid":
            raise OutputContractError(f"{path or tp.__name__}: output models must forbid extras")
        return schema_fields(tp, f"{path}." if path else "")
    raise OutputContractError(f"{path}: unsupported output type {tp!r}")


def classify(
    model: type[BaseModel],
    cited_fields: tuple[tuple[str, str], ...],
    text_fields: tuple[str, ...] = (),
) -> dict[str, str]:
    """{path: kind} for every string path of the model. Raises `OutputContractError` when a text
    path is covered by no rule, a declared path does not exist or is not text, a citation field
    is not a list of strings, or a field is declared twice."""
    if model.model_config.get("extra") != "forbid":
        raise OutputContractError("the output model must forbid extra fields")
    found = {f.path: f.kind for f in schema_fields(model)}
    declared_text = [t for t, _ in cited_fields] + list(text_fields)
    citations = [c for _, c in cited_fields]
    if len(set(declared_text)) != len(declared_text):
        raise OutputContractError("a text field is declared more than once")
    for text_path in declared_text:
        if found.get(text_path) != "text":
            raise OutputContractError(f"{text_path}: declared text field is not a string field")
    for text_path, cite_path in cited_fields:
        if found.get(f"{cite_path}[]") != "text" or cite_path + "[]" in declared_text:
            raise OutputContractError(f"{cite_path}: a citation field must be a list of strings")
        if _parent(text_path) != _parent(cite_path):
            raise OutputContractError(f"{text_path}: its citation field must be a sibling")
    kinds: dict[str, str] = {}
    for path, kind in found.items():
        if kind == "constrained":
            kinds[path] = "constrained"
        elif path.endswith("[]") and path[:-2] in citations:
            kinds[path] = "citation"
        elif path in declared_text:
            kinds[path] = "text"
        else:
            raise OutputContractError(
                f"{path}: output text is not covered by the shared checks; declare it in "
                "cited_fields (with its citations) or text_fields"
            )
    return kinds


def _parent(path: str) -> str:
    return path.rsplit(".", 1)[0] if "." in path else ""


@dataclass(frozen=True)
class TextLeaf:
    path: str  # schema path ("points[].text")
    where: str  # concrete location ("points.1.text"), for findings
    text: str
    citations: tuple[str, ...]  # empty for uncited text
    cited: bool  # True when the field makes factual claims with citations


def text_leaves(
    out: BaseModel,
    cited_fields: tuple[tuple[str, str], ...],
    text_fields: tuple[str, ...] = (),
) -> tuple[list[TextLeaf], list[str]]:
    """(every text value in the output with its citations, every string at a path no rule
    covers). Citation lists are returned as part of their text leaf, not as leaves."""
    cite_of = dict(cited_fields)
    citation_paths = {c for _, c in cited_fields}
    uncited = set(text_fields)
    leaves: list[TextLeaf] = []
    stray: list[str] = []

    def visit(value, path: str, where: str, parent: Any) -> None:
        if isinstance(value, BaseModel):
            for name in type(value).model_fields:
                sub = f"{path}.{name}" if path else name
                visit(getattr(value, name), sub, f"{where}.{name}" if where else name, value)
        elif isinstance(value, (list, tuple, set, frozenset)):
            if path in citation_paths:
                return  # read with its text field
            for i, v in enumerate(value):
                visit(v, f"{path}[]", f"{where}.{i}", parent)
        elif isinstance(value, enum.Enum):
            return
        elif isinstance(value, str):
            if path in cite_of:
                name = cite_of[path].rsplit(".", 1)[-1]
                cites = tuple(str(c) for c in (getattr(parent, name, None) or ()))
                leaves.append(TextLeaf(path, where, value, cites, True))
            elif path in uncited:
                leaves.append(TextLeaf(path, where, value, (), False))
            elif not _literal_path(type(parent), path):
                stray.append(where)

    visit(out, "", "", None)
    return leaves, stray


def _literal_path(parent_type, path: str) -> bool:
    """True when the string sits in a constrained (Literal / Enum) field of its parent."""
    if not (isinstance(parent_type, type) and issubclass(parent_type, BaseModel)):
        return False
    name = path.rsplit(".", 1)[-1].removesuffix("[]")
    info = parent_type.model_fields.get(name)
    if info is None:
        return False
    try:
        return all(f.kind == "constrained" for f in _walk(info.annotation, name))
    except OutputContractError:
        return False
