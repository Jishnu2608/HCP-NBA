"""Claude provider (optional). Enable with NBA_LLM_PROVIDER=claude and `pip install anthropic`.

Credentials are resolved by the SDK from the environment (ANTHROPIC_API_KEY, or an
`ant auth login` profile). Only synthetic profile data is ever sent.
"""

from app.llm.base import DraftOutput, DraftRequest, DraftResult
from app.nba.rationale import ACTION_LABEL, CHANNEL_LABEL

MODEL = "claude-opus-5-5"

SYSTEM = """You draft outreach wording for a healthcare engagement team.

A separate rules engine has already decided who to contact, the action, the channel and \
the approved content module. Those decisions are final and are not yours to change.

Your job is wording only:
- Write 2 message variants for the stated channel, in a warm, plain, respectful tone.
- Stay strictly within the approved content. Add no medical claims, statistics, dosing \
advice, links or promises that are not in it.
- Use the recipient's name as given. Leave no placeholders.
- Text messages: no subject, at most 300 characters. Email and portal messages: include \
a subject. Phone and in-person channels: write a short numbered guide for the staff member.
- Write a two-sentence rationale summary for the human reviewer, using only the reasons \
provided. Do not invent reasons.
"""

# Structured-output schema: every object closed, every field required.
SCHEMA = {
    "type": "object",
    "properties": {
        "rationale_summary": {"type": "string"},
        "variants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": ["string", "null"]},
                    "body": {"type": "string"},
                },
                "required": ["subject", "body"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["rationale_summary", "variants"],
    "additionalProperties": False,
}


def build_prompt(r: DraftRequest) -> str:
    recipient = (
        f"Dr. {r.last_name} ({r.specialty})"
        if r.target_type == "HCP"
        else f"{r.first_name} {r.last_name} (patient)"
    )
    reasons = "\n".join(f"- {text}" for text in r.reasons)
    return (
        f"Recipient: {recipient}\n"
        f"Action: {ACTION_LABEL.get(r.action, r.action)}\n"
        f"Channel: {CHANNEL_LABEL.get(r.channel, r.channel)}\n"
        f"Medication: {r.drug_name or 'not applicable'}\n\n"
        f"Approved content module {r.content_id}: {r.content_title}\n"
        f"{r.content_body}\n\n"
        f"Reasons established by the engine:\n{reasons}"
    )


class ClaudeProvider:
    name = "claude"

    def __init__(self) -> None:
        import anthropic  # optional dependency, imported only when this provider is selected

        self._client = anthropic.Anthropic()

    def draft(self, request: DraftRequest) -> DraftResult:
        response = self._client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(request)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            # If the primary model declines, the API re-runs the request on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason != "end_turn":
            raise RuntimeError(f"model stopped with {response.stop_reason}")
        text = next(block.text for block in response.content if block.type == "text")
        return DraftResult(
            output=DraftOutput.model_validate_json(text),
            provider=self.name,
            model=response.model,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )
