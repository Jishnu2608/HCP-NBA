"""Read services: the one place where context-relevant records are assembled for a reader.

Every function here takes the database session and the *acting user*, and:

- checks the permission itself (`core/permissions.can`), so calling a service directly is
  never a way around `require_permission`;
- applies record-level scope in the query (`core/rbac`), raising `NotFound` for a record the
  reader may not see, exactly as the API answers (out of scope looks like missing);
- returns typed Pydantic models (`services/schemas.py`), never ORM objects;
- has no HTTP dependency (no FastAPI, no request) and imports nothing from `app.llm` or any
  future AI module, so the routers, a mobile client and a future server-side context layer
  all read through the same code.

Writes stay where they are (clinical, commercial, content governance, delivery); these
services only read. Side effects that belong to a screen (marking messages read, recording
that a profile was viewed) stay in the routers, which decide what a page view means.
"""
