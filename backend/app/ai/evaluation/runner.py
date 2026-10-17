"""Runs the AI context evaluation and records its outcome.

`run(db, admin)`:

1. builds two isolated synthetic fixture worlds for this run (a main world and a fault world
   that fault-injection scenarios may damage), never the application database;
2. runs every predefined scenario (`scenarios.SCENARIOS`) through the real shared services;
3. scores the evidence with the fixed rubric (`rubric.score`), separately from the pass rate;
4. compares with the previous run, read from the audit log;
5. records the run in the audit log (codes, counts and per-scenario statuses only).

The application database receives the audit row (and the route's rate-limit row) only. The
panel cannot pass SQL, sources, payloads, policies or free text to any of this.
"""

import hashlib
import importlib
import json
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.ai import fixtures, registry
from app.ai.evaluation import probes, rubric
from app.ai.evaluation.scenarios import COVERAGE, SCENARIOS, Env, NotApplicable
from app.core.config import REPO_ROOT, get_settings
from app.models import AuditLog, User
from app.models.tables import utcnow

ACTION = "ai_evaluation_run"

# The findings of the 2026-10-17 self-audit and this remediation, with the scenarios that
# reproduce each one. A finding's status shown in the panel is derived from those scenarios
# when it has any; otherwise from the recorded evidence (tests, documents, owner decisions).
FINDINGS: tuple[dict, ...] = (
    {
        "id": "F-01",
        "severity": "High",
        "title": "Output checks bound to cited_fields (probe P4)",
        "recorded": "verified_closed",
        "evidence": "tests/test_context_remediation.py (P4 tests)",
    },
    {
        "id": "F-02",
        "severity": "Medium",
        "title": "Source SQL failure rolled back caller work",
        "recorded": "verified_closed",
        "evidence": "SAVEPOINT; test_failed_read_keeps_caller_work",
    },
    {
        "id": "F-03",
        "severity": "Low",
        "title": "Paraphrased absence claims passed",
        "recorded": "partially_resolved",
        "evidence": "Six paraphrase forms now caught; lexical checks remain non-semantic",
    },
    {
        "id": "F-04",
        "severity": "Low",
        "title": "Adjectival status not checked",
        "recorded": "verified_closed",
        "evidence": "STATUS_ADJECTIVE and cited-status contradiction",
    },
    {
        "id": "F-05",
        "severity": "Medium",
        "title": "No Ollama outbound firewall rule",
        "recorded": "external_verification_required",
        "evidence": "scripts/verify_ollama_egress.ps1 exits 1 (NOT VERIFIED); owner deferred",
    },
    {
        "id": "F-06",
        "severity": "Low",
        "title": "Tautological legacy allowlist check",
        "recorded": "verified_closed",
        "evidence": "engine._assert_model_view with the consumer list",
    },
    {
        "id": "F-07",
        "severity": "Low",
        "title": "Random pseudonym key without NBA_JWT_SECRET",
        "recorded": "verified_closed",
        "evidence": "pseudonym fails closed; test_pseudonym_* (separate processes agree)",
    },
    {
        "id": "F-08",
        "severity": "Low",
        "title": "max_age_days accepted but never applied",
        "recorded": "verified_closed",
        "evidence": "engine._freshness; CMP-FRESH scenarios",
    },
    {
        "id": "F-09",
        "severity": "Low",
        "title": "Diagnostics reads not audited or rate limited",
        "recorded": "verified_closed",
        "evidence": "test_diagnostics_reads_are_audited_and_limited",
    },
    {
        "id": "F-10",
        "severity": "Low",
        "title": "Context budget truncation untested",
        "recorded": "verified_closed",
        "evidence": "test_budget_truncation; CMP-TRUNC-01",
    },
    {
        "id": "F-11",
        "severity": "Info",
        "title": "Replay #8 called a genuine catch",
        "recorded": "verified_closed",
        "evidence": "All three replay rejections were false positives (reports corrected)",
    },
    {
        "id": "F-12",
        "severity": "Low",
        "title": "Read-only tooling changed the AI file (WAL)",
        "recorded": "verified_closed",
        "evidence": "app.core.db_snapshot; test_snapshot_*",
    },
    {
        "id": "F-13",
        "severity": "Low",
        "title": "GET routes that write",
        "recorded": "out_of_scope",
        "evidence": "Not reachable from context reads (read-only guard; FAI-RO scenarios)",
    },
    {
        "id": "F-14",
        "severity": "Info",
        "title": "Policy boundaries awaiting owner decisions",
        "recorded": "blocked_by_owner_decision",
        "evidence": "EXT-POL scenarios keep them closed",
    },
    {
        "id": "F-15",
        "severity": "Info",
        "title": "Frontend diagnostics not browser-verified",
        "recorded": "partially_resolved",
        "evidence": "Component tests only; browser behaviour unverified",
    },
    {
        "id": "F-16",
        "severity": "Medium",
        "title": "Prompt could carry non-model items",
        "recorded": "verified_closed",
        "evidence": "orchestrator._assert_model_items_only",
    },
    {
        "id": "F-17",
        "severity": "Medium",
        "title": "Registration accepted unknown or diagnostics permissions",
        "recorded": "verified_closed",
        "evidence": "registry.validate permission checks",
    },
    {
        "id": "F-18",
        "severity": "Low",
        "title": "mode_setting could name any setting",
        "recorded": "verified_closed",
        "evidence": "registry.validate mode_setting check",
    },
    {
        "id": "F-19",
        "severity": "Low",
        "title": "Retrieved passages not screened for injection",
        "recorded": "verified_closed",
        "evidence": "retrieval.retrieve drops flagged passages",
    },
    {
        "id": "F-20",
        "severity": "Info",
        "title": "No multi-subject generation contract (envelope limit)",
        "recorded": "accepted_limitation",
        "evidence": "Consumers needing several records use collection or aggregate subjects",
    },
)


def revision() -> str | None:
    """The commit at HEAD, read from .git without running git. Uncommitted changes are not
    detected."""
    git = Path(REPO_ROOT) / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head[5:]
            path = git / ref
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()[:12]
            packed = (git / "packed-refs").read_text(encoding="utf-8")
            for line in packed.splitlines():
                if line.endswith(" " + ref):
                    return line.split()[0][:12]
            return None
        return head[:12]
    except OSError:
        return None


def _run_scenarios(worlds: dict[str, Env]) -> list[dict]:
    results = []
    for s in SCENARIOS:
        env = worlds[s.world]
        env.current = s.id
        started = time.perf_counter()
        try:
            ok, actual = s.run(env)
            status = "pass" if ok else "fail"
        except NotApplicable as exc:
            status, actual = "not_applicable", str(exc)
        except Exception as exc:  # noqa: BLE001 - an unexpected error fails the scenario
            status, actual = "fail", f"error: {type(exc).__name__}"
        finally:
            env.db.rollback()
        results.append(
            {
                "id": s.id,
                "name": s.name,
                "control": s.control,
                "dimension": next(c.dimension for c in rubric.CONTROLS if c.id == s.control),
                "domain": s.domain,
                "role": s.role,
                "expected": s.expected,
                "actual": actual,
                "status": status,
                "gate": s.gate,
                "refs": list(s.refs),
                "finding": s.finding,
                "hard_blocker": s.hard_blocker,
                "trace": s.id in env.traces,
                "ms": int((time.perf_counter() - started) * 1000),
            }
        )
    return results


def _findings(results: list[dict]) -> list[dict]:
    out = []
    for f in FINDINGS:
        linked = [r for r in results if r["finding"] == f["id"]]
        status = f["recorded"]
        if linked and any(r["status"] == "fail" for r in linked):
            status = "open_regressed"
        out.append(
            {
                **f,
                "status": status,
                "scenarios": [r["id"] for r in linked],
                "scenarios_passed": sum(r["status"] == "pass" for r in linked),
            }
        )
    return out


def _previous(db: Session) -> dict | None:
    row = db.scalar(
        select(AuditLog).where(AuditLog.action == ACTION).order_by(AuditLog.id.desc()).limit(1)
    )
    if row is None or not row.detail:
        return None
    return {"at": row.ts.isoformat() if row.ts else None, **row.detail}


def _compare(previous: dict | None, results: list[dict]) -> dict:
    if not previous:
        return {"previous": None, "changes": []}
    before = previous.get("results", {})
    changes = []
    for r in results:
        old = before.get(r["id"])
        if old is not None and old != r["status"]:
            changes.append(
                {
                    "id": r["id"],
                    "from": old,
                    "to": r["status"],
                    "kind": "regression" if r["status"] == "fail" else "improvement",
                    # Only the scenario's own finding and tests are named: they reproduce exactly
                    # the behaviour that changed. No other cause is claimed.
                    "finding": r["finding"],
                    "refs": r["refs"],
                }
            )
    new = [r["id"] for r in results if r["id"] not in before]
    return {
        "previous": {
            k: previous.get(k)
            for k in ("at", "confidence", "readiness", "revision", "rubric", "counts")
        },
        "changes": changes,
        "new_scenarios": new,
    }


def transparency() -> dict:
    """The active prompt templates, configuration and policies the evaluation ran under."""
    settings = get_settings()
    ops = []
    for op in registry.registered():
        spec = registry.resolve(op)
        module = importlib.import_module(spec.build_messages.__module__)
        system = getattr(module, "SYSTEM", None)
        ops.append(
            {
                "id": spec.id,
                "version": spec.version,
                "prompt_version": spec.prompt_version,
                "subject_type": str(spec.subject_type),
                "categories": list(spec.context_kinds),
                "cited_fields": [list(c) for c in spec.cited_fields],
                "text_fields": list(spec.text_fields),
                "system_prompt": system,
            }
        )
    from app.ai.policy import POLICY_VERSION

    return {
        "operations": ops,
        "probe_prompt": {"version": probes.PROMPT_VERSION, "system": probes.SYSTEM},
        "config": {
            "provider": settings.ai_provider,
            "operation_modes": dict(settings.ai_operation_modes or {}),
            "operations_disabled": list(settings.ai_operations_disabled or []),
            "max_active_jobs": settings.ai_max_active_jobs,
            "policy_version": POLICY_VERSION,
            "rubric_version": rubric.RUBRIC_VERSION,
        },
    }


def run(db: Session, admin: User) -> dict:
    started = time.perf_counter()
    settings = get_settings()
    COVERAGE.clear()
    main_world, fault_world = fixtures._build(), fixtures._build()
    worlds = {
        "main": Env(world=main_world, db=main_world.session_factory(), settings=settings),
        "fault": Env(world=fault_world, db=fault_world.session_factory(), settings=settings),
    }
    try:
        results = _run_scenarios(worlds)
    finally:
        for env in worlds.values():
            env.db.rollback()
            env.db.close()
    findings = _findings(results)
    open_findings = [f for f in findings if f["status"] in ("open", "open_regressed")]
    scored = rubric.score(results, dict(COVERAGE), open_findings)
    counts = {
        "scenarios": len(results),
        "executed": sum(r["status"] in ("pass", "fail") for r in results),
        "passed": sum(r["status"] == "pass" for r in results),
        "failed": sum(r["status"] == "fail" for r in results),
        "not_applicable": sum(r["status"] == "not_applicable" for r in results),
        "skipped": 0,
        "unverified_controls": len(scored["unverified_controls"]),
    }
    pass_rate = round(100 * counts["passed"] / counts["executed"], 1) if counts["executed"] else 0
    digest = hashlib.sha256(json.dumps([[r["id"], r["status"]] for r in results]).encode())
    previous = _previous(db)
    traces = {}
    for env in worlds.values():
        traces.update(env.traces)
    out = {
        "run": {
            "at": utcnow().replace(microsecond=0).isoformat() + "Z",
            "revision": revision(),
            "rubric_version": rubric.RUBRIC_VERSION,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "results_digest": digest.hexdigest()[:16],
            "model_calls": 0,
            "worlds": "two synthetic fixture worlds built for this run (seed "
            f"{fixtures.SEED}); the application database is not read",
        },
        "counts": counts,
        "pass_rate": pass_rate,
        "score": scored,
        "scenarios": results,
        "traces": traces,
        "findings": findings,
        "external": [
            {"control": c["id"], "name": c["name"], "note": c["note"]}
            for c in scored["controls"]
            if c["kind"] == "external"
        ],
        "transparency": transparency(),
        "comparison": _compare(previous, results),
        "registered_operations": list(registry.registered()),
    }
    from app.ai.diagnostics import sanitize_report

    # Canaries, emails and record ids anywhere; fixture names in the data-derived parts (the
    # observed results and the traces), where a name would mean a leak. The fixed rubric and
    # scenario wording is not data ("mark" is a word, not only a fixture first name).
    sanitize_report({k: v for k, v in out.items() if k != "traces"}, [])
    sanitize_report(
        {"observed": [r["actual"] for r in results], "traces": traces},
        main_world.identifiers + fault_world.identifiers,
    )
    audit.record(
        db,
        ACTION,
        "ai_evaluation",
        "context_layer",
        actor=admin.username,
        actor_role=admin.role,
        actor_user_id=admin.id,
        detail={
            "rubric": rubric.RUBRIC_VERSION,
            "confidence": scored["confidence"],
            "readiness": scored["readiness"],
            "revision": out["run"]["revision"],
            "counts": counts,
            "pass_rate": pass_rate,
            "digest": out["run"]["results_digest"],
            "results": {r["id"]: r["status"] for r in results},
        },
    )
    db.commit()
    return out


def export(result: dict) -> dict:
    """The sanitized export: everything but the payload text of the traces."""
    from app.ai.diagnostics import sanitize_report

    traces = {}
    for sid, t in result.get("traces", {}).items():
        prompt = {
            k: v
            for k, v in t["prompt"].items()
            if k not in ("payload_sent", "payload_built_not_sent")
        }
        traces[sid] = {**t, "prompt": prompt}
    report = {**result, "traces": traces, "export": "sanitized: payload text removed"}
    return sanitize_report(report, [])


def last(db: Session) -> dict | None:
    return _previous(db)
