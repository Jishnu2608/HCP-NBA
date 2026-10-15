"""Contracts shared by every AI artifact, whatever the provider.

These pin the shape of the future AI layer; nothing in the application uses them yet.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Sensitivity(StrEnum):
    OPERATIONAL = "operational"  # dates, counts, statuses, content ids
    PERSONAL = "personal"  # names, contact details: never sent to a model by default
    FREE_TEXT = "free_text"  # notes: untrusted data, may carry injected instructions


class ContextItem(BaseModel):
    """One piece of evidence a model may read and cite. Built by the context layer only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^ctx:[a-z_]+:\d+$")  # evidence id the model must cite
    kind: str  # patient, rx, pdc, consent, touch, nba, note, ...
    text: str  # what the model sees
    source: str  # provenance: table or service the item came from
    source_id: str  # row or record id in that source
    recorded_at: datetime | None = None  # when the underlying fact was recorded
    sensitivity: Sensitivity = Sensitivity.OPERATIONAL


class GenerationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(system|user|assistant)$")
    content: str


class GenerationRequest(BaseModel):
    """What an artifact asks of a provider. Identical for local and cloud providers."""

    model_config = ConfigDict(extra="forbid")

    artifact: str  # e.g. "case_brief"
    prompt_version: str
    messages: list[GenerationMessage] = Field(min_length=1)
    json_schema: dict  # output contract the provider must enforce or the gate will reject
    contains_identifiable_data: bool = False
    temperature: float = 0.0
    seed: int | None = 7
    max_output_tokens: int = Field(default=1200, gt=0, le=8000)


class GenerationResult(BaseModel):
    """Raw provider output. Untrusted until the artifact's safety gate has checked it."""

    model_config = ConfigDict(extra="forbid")

    text: str
    provider: str
    model: str
    is_local: bool
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class ArtifactSpec:
    """Per-artifact policy. Each artifact declares these; the shared layer enforces them."""

    name: str
    permission: str  # checked on the server before any context is built
    context_kinds: tuple[str, ...]  # minimum-necessary: the only item kinds it may receive
    prompt_version: str
    allow_cloud: bool = False  # an artifact opts in to cloud providers explicitly
