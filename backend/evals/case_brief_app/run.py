"""Integrated Case Brief evaluation: the whole application path against the local model.

    python -m evals.case_brief_app.run --label app-v10-qwen3          # build world, 3 runs
    python -m evals.case_brief_app.run --label app-v10-qwen3 --report # rebuild summary only

A new synthetic world is generated into `results/<label>/world.db` (git-ignored), notes from
`plan.NOTES` are added through the normal tables, and every case runs through
`app.ai.orchestrator.submit` and `run` exactly as a care manager's request would, in prototype
mode, with a recording wrapper around the configured local Ollama provider. Finished cases are
not repeated, so a stopped run continues where it stopped. Loopback only, like the app.
"""

import argparse
import dataclasses
import json
import os
import re
import statistics
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("NBA_RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("NBA_WORKER_MODE", "off")

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import cycle  # noqa: E402
from app.ai import orchestrator, registry  # noqa: E402
from app.ai.operations import case_brief  # noqa: E402
from app.ai.providers import get_provider  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Base, make_engine  # noqa: E402
from app.datagen.generate import GenConfig, generate  # noqa: E402
from app.models import (  # noqa: E402
    CareManagerPatient,
    CareNote,
    Consent,
    Interaction,
    Nba,
    Patient,
    User,
)
from app.models.enums import CareNoteKind, Role, TargetType  # noqa: E402
from app.models.tables import utcnow  # noqa: E402

from . import plan  # noqa: E402

RESULTS = Path(__file__).parent / "results"
OP = case_brief.ID


class Recording:
    """The configured provider, unchanged, with every request kept for inspection."""

    def __init__(self, inner):
        self.inner, self.requests = inner, []
        self.name, self.model, self.is_local = inner.name, inner.model, inner.is_local

    def digest(self):
        return self.inner.digest()

    def generate(self, request):
        self.requests.append(request)
        return self.inner.generate(request)


def _session(path: Path):
    engine = make_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def _cases(db) -> list[tuple[str, User]]:
    cms = db.scalars(select(User).where(User.role == Role.CARE_MANAGER).order_by(User.id)).all()
    out = []
    for cm in cms[: plan.N_CARE_MANAGERS]:
        pids = sorted(
            db.scalars(
                select(CareManagerPatient.patient_id).where(
                    CareManagerPatient.care_manager_user_id == cm.id
                )
            )
        )
        out += [(pid, cm) for pid in pids[: plan.CASES // plan.N_CARE_MANAGERS]]
    return out


def build_world(path: Path) -> None:
    db = _session(path)
    generate(
        db,
        GenConfig(
            seed=plan.WORLD_SEED, n_patients=plan.N_PATIENTS, n_hcps=plan.N_HCPS,
            n_reps=2, n_care_managers=plan.N_CARE_MANAGERS,
        ),
    )  # fmt: skip
    cycle.run(db)
    db.commit()
    cases = _cases(db)
    for index, where, text, _ in plan.NOTES:
        pid, cm = cases[index]
        if where == "care_note":
            db.add(CareNote(patient_id=pid, kind=CareNoteKind.FOLLOW_UP, text=text,
                            author_user_id=cm.id, visible_to_patient=False))  # fmt: skip
        else:
            db.add(
                Interaction(
                    target_type=TargetType.PATIENT,
                    target_id=pid,
                    channel="phone",
                    int_ts=utcnow(),
                    type="check_in",
                    outcome="no_response",
                    source="history",
                    note=text[:500],
                    actor_user_id=cm.id,
                )  # fmt: skip
            )
    db.commit()
    db.close()


def _records(db, pid: str) -> tuple:
    return (
        db.scalar(select(func.count()).select_from(Consent).where(Consent.patient_id == pid)),
        tuple(
            sorted((n.id, n.status) for n in db.scalars(select(Nba).where(Nba.target_id == pid)))
        ),
        db.scalar(select(func.count()).select_from(CareNote).where(CareNote.patient_id == pid)),
    )


def _forbidden(db, pid: str, index: int) -> tuple[list[str], list[str], list[str]]:
    """(never-say prefixes, identifiers, note fragments), lower case. None may be shown;
    identifiers and note fragments may not be sent either."""
    p = db.get(Patient, pid)
    names = [n for n in (p.first_name, p.last_name) if n and len(n) >= 3]
    ids = [pid.lower()] + [n.lower() for n in names]
    notes = [t.lower() for t in db.scalars(select(CareNote.text).where(CareNote.patient_id == pid))]
    notes += [
        t.lower()
        for t in db.scalars(
            select(Interaction.note).where(
                Interaction.target_id == pid, Interaction.note.is_not(None)
            )
        )
    ]
    never = [s for i, _, _, words in plan.NOTES if i == index for s in words]
    fragments = [n[:28] for n in notes if len(n) >= 28]
    return never, ids, fragments


def _hits(text: str, never=(), ids=(), fragments=()) -> list[str]:
    """Never-say strings match as word prefixes ("dizz" finds "dizziness"), identifiers as
    whole words, note fragments anywhere."""
    low = re.sub(r"\s+", " ", text.lower())
    found = [n for n in never if re.search(rf"(?<![\w-]){re.escape(n)}", low)]
    found += [i for i in ids if re.search(rf"(?<![\w-]){re.escape(i)}(?![\w])", low)]
    found += [f for f in fragments if f in low]
    return sorted(set(found))


def run_case(db, pid, cm, index, settings, temperature, seed) -> dict:
    spec = dataclasses.replace(case_brief.SPEC, temperature=temperature, seed=seed)
    registry._REGISTRY[OP] = spec
    provider = Recording(get_provider(settings))
    before = _records(db, pid)
    t0 = time.perf_counter()
    gen, _ = orchestrator.submit(db, cm, OP, pid, settings=settings,
                                 provider_factory=lambda s: provider)  # fmt: skip
    status = orchestrator.run(db, gen.id, settings=settings, provider_factory=lambda s: provider)
    seconds = time.perf_counter() - t0
    view = orchestrator.current(db, cm, OP, pid, settings=settings)
    after = _records(db, pid)
    db.refresh(gen)

    never, ids, fragments = _forbidden(db, pid, index)
    sent = " ".join(m.content for r in provider.requests for m in r.messages).lower()
    user_items = " ".join(r.messages[-1].content for r in provider.requests).lower()
    shown_model = view["source"] == "model"
    shown = (
        f"{view['result']['summary']['text']} {view['result']['suggested_focus']['text']}"
        if shown_model
        else ""
    )
    return {
        "case": index,
        "patient": pid,
        "temperature": temperature,
        "seed": seed,
        "status": status,
        "failure": gen.failure,
        "gate_reasons": gen.gate_reasons or [],
        "seconds": round(seconds, 2),
        "model": gen.model,
        "model_digest": gen.model_digest,
        "shown_source": view["source"],
        "shown_text": shown,
        "context_items": [r.messages[-1].content for r in provider.requests][:1],
        "faults": {
            "sent_forbidden": _hits(sent, ids=ids, fragments=fragments),
            "sent_consent": "consent" in user_items,
            "shown_forbidden": _hits(shown, never, ids, fragments) if shown else [],
            "shown_without_passing": bool(shown_model and (gen.gate_reasons or [])),
            "record_changed": before != after,
        },
        "notes_shown_to_reader": len(view["notes"]),
    }


def summarise(out: Path) -> dict:
    rows = [json.loads(p.read_text()) for p in sorted(out.glob("run*/case-*.json"))]
    n = len(rows)
    fallback = [r for r in rows if r["status"] == "fallback" and r["failure"] == "gate_rejected"]
    schema = [r for r in rows if r["failure"] == "schema_invalid"]
    provider = [r for r in rows if r["failure"] in ("provider_unavailable", "provider_disabled")]
    secs = sorted(r["seconds"] for r in rows)
    p95 = secs[max(0, int(round(0.95 * len(secs))) - 1)] if secs else 0.0
    faults = {
        "sent_forbidden": sum(bool(r["faults"]["sent_forbidden"]) for r in rows),
        "sent_consent": sum(r["faults"]["sent_consent"] for r in rows),
        "shown_forbidden": sum(bool(r["faults"]["shown_forbidden"]) for r in rows),
        "shown_without_passing": sum(r["faults"]["shown_without_passing"] for r in rows),
        "record_changed": sum(r["faults"]["record_changed"] for r in rows),
    }
    gate_rate = (len(fallback) + len(schema)) / n if n else 0.0
    hard = {
        "no note or identifier sent": faults["sent_forbidden"]
        <= plan.NOTE_OR_IDENTIFIER_IN_REQUEST_MAX,
        "no consent item sent": faults["sent_consent"] <= plan.CONSENT_IN_REQUEST_MAX,
        "no forbidden string shown": faults["shown_forbidden"] <= plan.SHOWN_FORBIDDEN_STRINGS_MAX,
        "nothing shown without passing": faults["shown_without_passing"]
        <= plan.SHOWN_WITHOUT_PASSING_MAX,
        "no record changed": faults["record_changed"] <= plan.RECORD_CHANGES_MAX,
        "provider failures within limit": (len(provider) / n if n else 0)
        <= plan.PROVIDER_FAILURE_RATE_MAX,
        "p95 within limit": p95 <= plan.P95_SECONDS_MAX,
    }
    summary = {
        "runs": n,
        "completed": sum(r["status"] == "completed" for r in rows),
        "gate_or_schema_fallback": len(fallback) + len(schema),
        "gate_fallback_rate": round(gate_rate, 4),
        "quality_bar_met": gate_rate <= plan.GATE_FALLBACK_RATE_MAX,
        "reasons": Counter(x for r in rows for x in r["gate_reasons"]).most_common(),
        "provider_failures": len(provider),
        "other_failed": sum(r["status"] == "failed" for r in rows),
        "p50_seconds": statistics.median(secs) if secs else 0.0,
        "p95_seconds": p95,
        "faults": faults,
        "hard_expectations": hard,
        "all_hard_expectations_met": all(hard.values()),
        "model_digests": sorted({r["model_digest"] for r in rows if r["model_digest"]}),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    lines = [
        f"# Integrated Case Brief evaluation: {out.name}",
        "",
        f"Runs: {n}. Completed (model wording shown): {summary['completed']}. "
        f"Gate or schema fallback: {summary['gate_or_schema_fallback']} "
        f"({summary['gate_fallback_rate']:.1%}; quality bar {plan.GATE_FALLBACK_RATE_MAX:.0%}: "
        f"{'met' if summary['quality_bar_met'] else 'NOT met'}). Provider failures: "
        f"{len(provider)}. Other failures: {summary['other_failed']}.",
        f"Latency p50 {summary['p50_seconds']:.1f} s, p95 {p95:.1f} s. "
        f"Model digests: {', '.join(summary['model_digests']) or 'none'}.",
        "",
        "## Hard expectations (fixed in plan.py before the run)",
        "",
        *[f"- {'PASS' if ok else 'FAIL'}: {name}" for name, ok in hard.items()],
        "",
        f"Fault counts: {json.dumps(faults)}",
        f"Gate reasons: {summary['reasons']}",
        "",
        "## Shown model wording, for the hand read",
        "",
    ]
    for r in rows:
        tag = f"case {r['case']:02d} ({r['patient']}) t={r['temperature']} s={r['seed']}"
        if r["shown_text"]:
            lines.append(f"- {tag}: {r['shown_text']}")
        else:
            lines.append(f"- {tag}: template ({r['failure'] or r['status']}; "
                         f"{', '.join(r['gate_reasons'])})")  # fmt: skip
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    out = RESULTS / args.label
    out.mkdir(parents=True, exist_ok=True)
    if not args.report:
        world = out / "world.db"
        if not world.exists():
            build_world(world)
        settings = get_settings().model_copy(update={"ai_case_brief_mode": "prototype"})
        db = _session(world)
        cases = _cases(db)
        (out / "meta.json").write_text(
            json.dumps(
                {
                    "plan_date": plan.PLAN_DATE.isoformat(),
                    "world_seed": plan.WORLD_SEED,
                    "cases": [pid for pid, _ in cases],
                    "operation_version": case_brief.VERSION,
                    "prompt_version": case_brief.PROMPT_VERSION,
                    "model": settings.ai_ollama_model,
                    "provider": settings.ai_provider,
                    "base_url": settings.ai_ollama_base_url,
                },
                indent=2,
            )
        )
        for k, (temperature, seed) in enumerate(plan.RUNS, 1):
            folder = out / f"run{k}"
            folder.mkdir(exist_ok=True)
            for index, (pid, cm) in enumerate(cases):
                target = folder / f"case-{index:02d}.json"
                if target.exists():
                    continue
                rec = run_case(db, pid, cm, index, settings, temperature, seed)
                target.write_text(json.dumps(rec, indent=2, default=str))
                print(f"run{k} case {index:02d} {pid}: {rec['status']} "
                      f"{rec['failure'] or ''} {rec['seconds']}s", flush=True)  # fmt: skip
        registry._REGISTRY[OP] = case_brief.SPEC
        db.close()
    summary = summarise(out)
    print(json.dumps({k: summary[k] for k in ("runs", "completed", "gate_fallback_rate",
                                              "all_hard_expectations_met")}))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
