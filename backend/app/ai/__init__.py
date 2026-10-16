"""Shared AI infrastructure. See docs/ai-architecture.md.

Modules, outermost first:

- `registry` and `operations/`: approved operations, each an `OperationSpec` (identifier,
  version, permission, context allowlist, prompt, output schema, checks, fallback). Only
  registered operations exist. Case Brief is the one registered today.
- `orchestrator`: the single generation path (submit, run as a background job, current
  result), with mode, rate and capacity controls, staleness, audit and fallback.
- `context` and `context_policy`: the context service. Authorisation, consent and scope are
  decided by the read services (`app.services`) for the acting user; every field is classified
  before anything is built; free text and identifiers never reach a model.
- `safety`: shared deterministic checks on model text. A model never judges a model, and a
  model cannot mark its own output safe.
- `providers`: local Ollama only. Cloud inference is refused in this phase; a provider that is
  off or failing means the operation's deterministic template, never another provider.
- `contracts`: strict, provider- and operation-neutral types for all of the above.

Core business modules never import this package (enforced by tests): NBA eligibility, consent,
MLR approval, delivery and access control stay deterministic and authoritative. Model output
never writes a record, a consent, a permission or a workflow state.
"""
