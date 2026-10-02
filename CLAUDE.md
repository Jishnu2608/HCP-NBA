# Project instructions

## Workflow_Context.md is the project's implementation context

- Before starting any major implementation task, read `Workflow_Context.md` at the repository root and use it as project context.
- After completing any meaningful change, update `Workflow_Context.md` in the same piece of work: implementation, architecture change, module completion, schema change, route change, UI change, RBAC change, authentication change, or major bug fix.
- Update the existing section in place so the file describes the current state. Do not append a chronological log.
- Keep the Completed / In Progress / Planned / Known Issues sections accurate, and refresh "Last verified against code" and "Next Recommended Task".
- If the code and `Workflow_Context.md` disagree, inspect the code and correct the file.
- Keep it concise; do not copy the README or source code into it.

## Working conventions

- Backend commands run from `backend` with `.venv\Scripts\python`. Tests: `python -m pytest`. Lint: `ruff check .` and `ruff format .`.
- After changing tables, keep Alembic in sync (`alembic check` must report no new operations).
- After changing frontend code, run `npm run typecheck` and `npm run build` in `frontend`; the API serves `frontend/dist`.
- All data is synthetic. Never add real patient or HCP data.
