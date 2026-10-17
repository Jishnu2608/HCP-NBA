"""The inference trace of one evaluation pipeline run: operational evidence only.

It shows what the shared layer decided at each step: the consumer contract, the principal,
the source and its provenance, every category state with its reason, the policy decision per
category, the prompt template and the payload, the output contract, the checks and their
findings, the provider and the disposition. It never shows model reasoning (none exists: the
model is a scripted test double here) and it is built from the synthetic fixture world only.

The payload appears twice: `payload_sent` is exactly what the pipeline handed the provider
(synthetic, identifier-free by construction, and checked for canaries here), and
`payload_redacted` is the same messages with every item text replaced by its evidence id, which
is what a record-preview diagnostic would show for real data.
"""

import json
import re

from app.ai import fixtures, output_contract
from app.ai.sources import source_for

SHARED_CHECKS = (
    "invalid_citation",
    "fabricated_date",
    "fabricated_number",
    "fabricated_id",
    "fabricated_drug",
    "fabricated_condition",
    "clinical_claim",
    "unsafe_medication",
    "fabricated_recommendation",
    "consent_claim",
    "action_claim",
    "leak:contact",
    "leak:link",
    "leak:identifier",
    "leak:free_text",
    "unsupported_absence",
    "unverifiable_absence",
    "fabricated_status",
    "contradiction:status",
    "fabricated_code",
    "leak:instructions",
    "injection_echo",
    "miscited",
    "unchecked_field",
    "schema",
)


def _redact(content: str, ctx) -> str:
    out = content
    for item in ctx.model_items if ctx is not None else []:
        out = out.replace(item.text, f"<{item.category or item.kind} item {item.id}>")
    return re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "<date>", out)


def trace_view(spec, result, provider, role: str) -> dict:
    ctx = result.ctx
    source = source_for(spec.subject_type)
    request = provider.requests[0] if provider.requests else result.request
    sent = bool(provider.requests)
    messages = [m.model_dump() for m in request.messages] if request is not None else []
    blob = json.dumps(messages)
    try:
        contract = output_contract.classify(
            spec.output_model, tuple(spec.cited_fields), tuple(spec.text_fields)
        )
        contract_error = None
    except output_contract.OutputContractError as exc:
        contract, contract_error = {}, str(exc)
    allow = set(spec.context_kinds)
    return {
        "contract": {
            "operation": spec.id,
            "test_only": bool(spec.tags.get("test_only")),
            "version": spec.version,
            "prompt_version": spec.prompt_version,
            "subject_type": str(spec.subject_type),
            "permission": spec.permission,
            "categories": list(spec.context_kinds),
            "allow_partial": spec.allow_partial,
            "max_age_days": spec.max_age_days,
            "max_context_items": spec.max_context_items,
            "max_output_chars": spec.max_output_chars,
        },
        "principal": {"role": role, "account": "synthetic fixture account"},
        "steps": result.trace,
        "source": {
            "id": source.id,
            "read_services": list(source.read_services),
            "principal_dependent": source.principal_dependent,
            "policy_version": ctx.policy_version if ctx else None,
            "fingerprint": ctx.fingerprint if ctx else None,
            "complete": ctx.complete if ctx else None,
            "evidence": [
                {
                    "id": i.id,
                    "category": i.category or i.kind,
                    "source": i.source,
                    "source_version": i.source_version,
                    "dated": (i.as_of or i.recorded_at).isoformat()[:10]
                    if (i.as_of or i.recorded_at)
                    else None,
                }
                for i in (ctx.model_items if ctx else [])
            ],
        },
        "policy": [
            {
                "category": c.name,
                "model_eligible": c.model,
                "in_allowlist": c.name in allow,
                "state": ctx.state_of(c.name).value if ctx and ctx.state_of(c.name) else None,
                "reason": next((s.reason for s in ctx.categories if s.category == c.name), None)
                if ctx
                else None,
            }
            for c in source.categories
        ],
        "prompt": {
            "template_version": spec.prompt_version,
            "system": next((m["content"] for m in messages if m["role"] == "system"), None),
            "sent": sent,
            "payload_sent": messages if sent else [],
            "payload_built_not_sent": messages if (request is not None and not sent) else [],
            "payload_redacted": [
                {"role": m["role"], "content": _redact(m["content"], ctx)} for m in messages
            ],
            "canaries_in_payload": sum(1 for c in fixtures.CANARIES if c in blob),
            "identifiers_in_payload": sum(
                1
                for n in (ctx.identifiers if ctx else [])
                if re.search(rf"\b{re.escape(n)}\b", blob)
            ),
        },
        "output_contract": {"paths": contract, "error": contract_error},
        "checks": {
            "shared": list(SHARED_CHECKS),
            "reasons": result.outcome.reasons if result.outcome else [],
            "findings": [f.model_dump() for f in result.outcome.findings] if result.outcome else [],
        },
        "provider": {
            "name": provider.name,
            "model": provider.model,
            "local": provider.is_local,
            "calls": len(provider.requests),
            "note": "scripted test double: no model is called by the evaluation",
        },
        "disposition": {
            "status": result.status.value,
            "failure": result.failure.value if result.failure else None,
            "output_stored": result.output is not None,
        },
    }
