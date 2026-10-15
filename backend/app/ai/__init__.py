"""Shared AI infrastructure (V2). Architecture prepared, not yet wired to any feature.

Layers, outermost first (see docs/ai-architecture.md):

1. Artifact service: one per AI artifact (Case Brief first). Owns its permission, output schema,
   prompt version, validation policy and deterministic fallback. Runs as a background job.
2. Context layer (`app.context`, later): builds typed, minimum-necessary `ContextItem`s through
   the scoped read services (`app.services`) for the acting user. Authorization, consent and
   scope are decided there, in code, before any model sees anything.
3. Safety gate: schema, citations, grounding, clinical-claim, leak and injection checks. Model
   text reaches a reader only when every check passes; otherwise the artifact's template.
4. Provider adapter (`app.ai.providers`): local Ollama by default; an organisation-funded cloud
   API only when the server is configured for it. Selected on the server, never by a client,
   and never switched silently: a failing provider means the deterministic fallback.

Core business modules never import this package (enforced by tests): NBA eligibility, consent,
MLR approval, delivery and access control stay deterministic and authoritative.
"""
