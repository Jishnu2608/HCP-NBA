"""Output contract for a Case Brief, and the schema handed to the model runtime."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeyPoint(_Strict):
    text: str = Field(min_length=1, max_length=300)
    citations: list[str] = Field(min_length=1, max_length=6)


class CaseBrief(_Strict):
    summary: str = Field(min_length=1, max_length=600)
    key_points: list[KeyPoint] = Field(min_length=2, max_length=8)
    suggested_focus: str = Field(min_length=1, max_length=400)
    not_recorded: list[str] = Field(max_length=8)


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(defs[node["$ref"].split("/")[-1]], defs)
        return {k: _inline(v, defs) for k, v in node.items() if k not in ("title", "$defs")}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def json_schema() -> dict[str, Any]:
    """Self-contained JSON schema (no $ref) so every runtime can build a grammar from it."""
    raw = CaseBrief.model_json_schema()
    return _inline(raw, raw.get("$defs", {}))
