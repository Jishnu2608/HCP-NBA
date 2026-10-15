# Shared AI layer: architecture

Status (2026-10-15): prepared, not built. The provider interface, contracts and configuration exist
in `backend/app/ai/`; no feature uses them yet. The Case Brief is still evaluated offline in
`backend/evals/case_brief/` and the deterministic template remains what a reader would see.

## Principles

- **Deterministic rules stay authoritative.** NBA eligibility, consent, MLR approval, contact
  limits, delivery and access control are decided in code. A model never decides, overrides or
  sees the outcome of an eligibility decision it could change. Core modules never import
  `app.ai` or `app.context` (enforced by `tests/test_read_services.py`).
- **The model only words what the server already decided it may say,** from evidence the server
  chose. Its output is untrusted until a deterministic gate has checked it.
- **One contract for every provider.** Local and cloud providers take the same
  `GenerationRequest` and return the same `GenerationResult`; the output schema, safety gate,
  audit record and template fallback do not depend on the provider.
- **No silent fallback.** The server is configured for one provider. If it is off, incomplete or
  failing, the artifact shows its deterministic template and records why. It never retries on
  another provider, and never moves from local to cloud on its own.

## Request path (one artifact call)

```
user action (authorised route, permission checked)
  -> artifact service           permission, minimum-necessary context kinds, prompt version
  -> context layer               scoped read services -> typed ContextItems (ids, provenance, time)
  -> data policy                 identifiable data stays local unless the operator allows it
  -> provider adapter            ollama (default) | cloud (prepared) | off
  -> safety gate                 schema, citations, grounding, claims, leaks, injection, policy
  -> reader sees model text      only if every check passes
     or the template brief       otherwise, with the reasons in the audit log
```

Generation runs as a background job (`app/jobs.py`), never inside a web request.

## Building blocks

| Block | Where | State |
|---|---|---|
| `ContextItem` (id `ctx:<kind>:<n>`, kind, text, source, source_id, recorded_at, sensitivity) | `app/ai/contracts.py` | Contract defined |
| `GenerationRequest` / `GenerationResult` | `app/ai/contracts.py` | Contract defined |
| `ArtifactSpec` (permission, allowed context kinds, prompt version, cloud opt-in) | `app/ai/contracts.py` | Contract defined |
| `ModelProvider` protocol, `OllamaProvider`, `CloudProvider`, `get_provider`, `check_data_policy` | `app/ai/providers.py` | Ollama adapter works; cloud adapter refuses to call |
| Context layer (built from `app/services/` for the acting user) | `app/context/` | Not built |
| Safety gate | `backend/evals/case_brief/gate.py` + `checks.py` | Proven offline; moves into `app/ai` with the first artifact |
| Audit of each generation (artifact, provider, model, prompt version, evidence ids, gate result, fallback reason) | existing `audit` table | Not built (no migration needed: audit detail is JSON) |
| Case Brief artifact | | Not built; blocked on the model passing the evaluation gates |

## Context rules

- Built only on the server, for the acting user, through the scoped read services. The model
  never queries the database and never receives a record the user could not open.
- Consent-aware: purposes the patient has not granted are left out, not masked.
- Minimum necessary: each artifact lists the item kinds it may receive (`ArtifactSpec.context_kinds`).
- Identity is attached by the server after generation. The model does not see or write patient
  ids, names or contact details.
- Free-text notes are marked `free_text`: data, never instructions.

## Provider configuration

Set in `.env` (see `.env.example`); never from a client.

| Variable | Meaning |
|---|---|
| `NBA_AI_PROVIDER` | `ollama` (default), `cloud`, `off` |
| `NBA_AI_OLLAMA_BASE_URL` | Loopback URL of the local Ollama server; anything else is refused |
| `NBA_AI_OLLAMA_MODEL` | Local model tag; tags containing `cloud` are refused |
| `NBA_AI_TIMEOUT_SECONDS` | Per-call timeout |
| `NBA_AI_CLOUD_VENDOR`, `NBA_AI_CLOUD_MODEL`, `NBA_AI_CLOUD_BASE_URL` | Organisation-funded cloud API, once chosen |
| `NBA_AI_CLOUD_API_KEY_ENV` | The **name** of the environment variable that holds the key; the key itself lives only in `.env` or the host's secret store |
| `NBA_ALLOW_EXTERNAL_IDENTIFIABLE_DATA` | Must be true before identifiable context may reach a non-local provider |

Local Ollama must itself run with `OLLAMA_NO_CLOUD=1` so it cannot reach hosted models.

## Adding the cloud adapter later

1. The organisation chooses a vendor, signs the data agreement and supplies a key.
2. Write the vendor adapter in `CloudProvider.generate`: request mapping, structured output,
   timeouts, error mapping to `ProviderError`. No retries onto another provider.
3. Rerun the artifact's evaluation on the cloud model with the same cases, gate and thresholds.
   A cloud model is held to the same hard gates as a local one.
4. Enable per artifact (`ArtifactSpec.allow_cloud`), not globally.

## Relation to the existing drafting layer

`app/llm/` (outreach wording for an already-approved recommendation, `NBA_LLM_PROVIDER`,
template by default) predates this design and keeps working unchanged. It moves onto the shared
provider interface when the first `app/ai` artifact ships.
