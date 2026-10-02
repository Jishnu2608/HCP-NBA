"""The drafting contract every model provider implements.

A provider receives a recommendation that has already passed every gate, plus the approved
content it must stay within. It returns wording only. It cannot change who is contacted,
on which channel, with which content, or whether the touch is allowed.
"""

from typing import Protocol

from pydantic import BaseModel, Field


class DraftRequest(BaseModel):
    nba_id: int
    target_type: str
    action: str
    channel: str
    content_id: str
    content_title: str
    content_body: str
    first_name: str
    last_name: str
    drug_name: str | None = None
    specialty: str | None = None
    # Reason sentences already established by the engine, in display order.
    reasons: list[str]


class MessageVariant(BaseModel):
    subject: str | None = None
    body: str


class DraftOutput(BaseModel):
    """What a provider must return. Also the JSON schema handed to a real model."""

    rationale_summary: str = Field(description="Two sentences, plain language, for a reviewer.")
    variants: list[MessageVariant] = Field(min_length=1, max_length=3)


class DraftResult(BaseModel):
    output: DraftOutput
    provider: str
    model: str | None = None
    usage: dict | None = None


class LLMProvider(Protocol):
    name: str

    def draft(self, request: DraftRequest) -> DraftResult: ...
