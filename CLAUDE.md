# Project instructions

## Persistent project context: three files, three jobs

| File | Answers | Update when |
|---|---|---|
| `Master_Build.md` | What the system is supposed to be: product, architecture, rules, decisions | Only when a requirement, architecture, role or permission, technology choice or system rule changes |
| `Workflow_Context.md` | What has actually been implemented | After every meaningful implementation change |
| `TODO.md` | What remains to be built, fixed, tested or improved | When work is discovered, started, completed or blocked |

Do not duplicate the same information across the three. Previous chat history may not be available: these files are the project's memory.

## At the start of every major task or new session

1. Read `Master_Build.md`.
2. Read `Workflow_Context.md`.
3. Read `TODO.md`.
4. Inspect the relevant source code.
5. Reconcile: the codebase is the final authority. If a context file is out of date, inspect the code, correct the file, then continue.
6. Only then plan or change code.

If the code contradicts a **rule** in `Master_Build.md` (sections 7 to 10 and 13: LLM boundaries, compliance gates, separation of duties, RBAC, assignment versus role), do not silently rewrite the rule to match the code. Tell the user.

## After every meaningful implementation

- Update `Workflow_Context.md` in place so it describes the current state: implementation, architecture change, module completion, schema change, route change, UI change, RBAC change, authentication change, or major bug fix. No chronological log. Refresh "Last verified against code" and "Next Recommended Task".
- Update `TODO.md`: mark completed work `[x]` only if implemented and verified, add newly discovered work, update in-progress and blocked items.
- Update `Master_Build.md` only if a product or architectural requirement or a major decision changed.
- Before finishing a major task, check that all three files are accurate.

## Rules that must not be broken

- All data is synthetic. Never add real patient or HCP data.
- Admin must not approve or reject MLR content; only the Compliance role may. Enforced in the API by permission.
- Authorise by permission, never by role name. Assignments never change a role.
- Gates (MLR, consent, frequency) are deterministic code. The LLM only words an already-eligible action.
- Never put real credentials in code, chat or commits. The user adds SMTP and other secrets to `.env` themselves.

## Working conventions

- Backend commands run from `backend` with `.venv\Scripts\python`. Tests: `python -m pytest`. Lint: `ruff check .` and `ruff format .`.
- After changing tables, add an Alembic migration and keep it in sync (`alembic check` must report no new operations).
- After changing frontend code, run `npm run typecheck` and `npm run build` in `frontend`; the API serves `frontend/dist`.
- Commit and push only when the user asks.
