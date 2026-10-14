"""Aggregate a results folder into gate verdicts, a scorecard and a winner (or none).

python -m evals.case_brief.report evals/case_brief/results/<run id>
"""

import json
import math
import sys
from collections import Counter
from pathlib import Path

from . import thresholds as T
from .cases import build_cases
from .checks import evaluate
from .schema import CaseBrief

_CASES = {c.id: c for c in build_cases()}


def rescore(rec: dict) -> dict:
    """Re-run the current checks on the stored answer, so a checker fix applies to every model."""
    final = rec["attempts"][-1] if rec["attempts"] else None
    if final and final["error"] is None:
        brief = CaseBrief.model_validate_json(final["output"])
        rec["evaluation"] = evaluate(_CASES[rec["case"]], brief)
    return rec


def _p(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[max(0, math.ceil(q * len(s)) - 1)] if s else float("nan")


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def _rubric(evals: list[dict]) -> float:
    return _mean([e["rubric"]["score"] for e in evals])


def summarize_model(recs: list[dict]) -> dict:
    n = len(recs)
    evals = [r["evaluation"] for r in recs if r["evaluation"]]
    kinds = Counter(c["kind"].split(":")[0] for e in evals for c in e["critical"])
    statements = sum(e["statements"] for e in evals)
    secs = [r["seconds"] for r in recs]
    gen_tokens = sum(a["gen_tokens"] for r in recs for a in r["attempts"])
    gen_ns = sum(a["gen_ns"] for r in recs for a in r["attempts"])
    parts = {
        k: _mean([e["rubric"][k] for e in evals if e["rubric"][k] is not None])
        for k in ("coverage", "barrier", "conflict", "missing")
    }
    m = {
        "cases": n,
        "schema_first_try": sum(r["first_try_valid"] for r in recs) / n,
        "schema_after_retry": sum(r["valid"] for r in recs) / n,
        "invalid_citations": kinds["invalid_citation"],
        "fabricated_facts": sum(v for k, v in kinds.items() if k.startswith("fabricated")),
        "clinical_claims": kinds["clinical_claim"],
        "leaks": kinds["leak"],
        "injection_echoes": kinds["injection_echo"],
        "miscited_rate": sum(e["miscited_statements"] for e in evals) / statements
        if statements
        else 0.0,
        "failure_rate": sum(r["failure"] or r.get("exception") is not None for r in recs) / n,
        "p50_s": _p(secs, 0.5),
        "p95_s": _p(secs, 0.95),
        "mean_s": _mean(secs),
        "gen_tokens_per_s": gen_tokens / (gen_ns / 1e9) if gen_ns else float("nan"),
        "peak_model_gib": max((r["model_gib"] or 0 for r in recs), default=0),
        "vram_gib": max((r["vram_gib"] or 0 for r in recs), default=0),
        "peak_system_gib": max((r["peak_system_gib"] or 0 for r in recs), default=0),
        "minor_unsupported_suggestions": sum(len(e["minor"]) for e in evals),
        "rubric": _rubric(evals),
        "rubric_parts": parts,
        "retries_used": sum(len(r["attempts"]) > 1 for r in recs),
    }
    m["gates"] = {
        "schema_first_try": m["schema_first_try"] >= T.SCHEMA_FIRST_TRY_MIN,
        "schema_after_retry": m["schema_after_retry"] >= T.SCHEMA_AFTER_RETRY_MIN,
        "invalid_citations": m["invalid_citations"] <= T.INVALID_CITATIONS_MAX,
        "fabricated_facts": m["fabricated_facts"] <= T.FABRICATED_FACTS_MAX,
        "clinical_claims": m["clinical_claims"] <= T.CLINICAL_CLAIMS_MAX,
        "leaks": m["leaks"] <= T.LEAKS_MAX,
        "injection_echoes": m["injection_echoes"] <= T.INJECTION_ECHOES_MAX,
        "miscited_rate": m["miscited_rate"] <= T.MISCITED_STATEMENT_RATE_MAX,
        "failure_rate": m["failure_rate"] <= T.FAILURE_RATE_MAX,
        "p95_seconds": m["p95_s"] <= T.P95_SECONDS_MAX,
        "peak_model_gib": m["peak_model_gib"] <= T.PEAK_MODEL_GIB_MAX,
    }
    m["passes_all_gates"] = all(m["gates"].values())
    return m


def choose(models: dict[str, dict], template_rubric: float) -> tuple[str | None, str]:
    ok = {k: v for k, v in models.items() if v["passes_all_gates"]}
    if not ok:
        return None, "No model passes every gate. Keep the deterministic template."
    useful = {k: v for k, v in ok.items() if v["rubric"] > template_rubric}
    if not useful:
        return None, "Models pass the gates but none beats the template rubric. Keep the template."
    best = max(v["rubric"] for v in useful.values())
    tied = {k: v for k, v in useful.items() if best - v["rubric"] <= T.TIE_BAND}
    winner = min(tied, key=lambda k: tied[k]["mean_s"])
    why = "highest rubric" if len(tied) == 1 else f"rubric tie within {T.TIE_BAND}, fastest"
    return winner, why


def examples(recs: list[dict], limit: int = 8) -> list[str]:
    out = []
    for r in recs:
        for c in (r["evaluation"] or {}).get("critical", []):
            out.append(f"{r['case']}: {c['kind']} in {c['where']}: {c['value']}")
    return out[:limit]


def write(run_dir: Path) -> dict:
    meta = json.loads((run_dir / "meta.json").read_text())
    base = json.loads((run_dir / "baseline.json").read_text())
    template_rubric = _rubric([b["evaluation"] for b in base])
    template_critical = sum(len(b["evaluation"]["critical"]) for b in base)

    models: dict[str, dict] = {}
    raw: dict[str, list[dict]] = {}
    for mdir in sorted(d for d in run_dir.iterdir() if d.is_dir()):
        recs = [rescore(json.loads(f.read_text())) for f in sorted(mdir.glob("*.json"))]
        if recs:
            raw[recs[0]["model"]] = recs
            models[recs[0]["model"]] = summarize_model(recs)
    winner, why = choose(models, template_rubric)

    summary = {
        "meta": meta,
        "template": {"rubric": template_rubric, "critical_faults": template_critical},
        "models": models,
        "winner": winner,
        "winner_reason": why,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    names = list(models)
    gate_keys = list(next(iter(models.values()))["gates"]) if models else []
    lines = [
        f"# Case Brief evaluation {meta['run_id']}",
        "",
        f"Ollama {meta['ollama_version']}, prompt v{meta['prompt_version']}, "
        f"{meta['cases']} cases, temperature 0.",
        f"Template baseline: rubric {template_rubric:.3f}, critical faults {template_critical}.",
        "",
        "| Measure | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]

    def row(label: str, fn) -> None:
        lines.append(f"| {label} | " + " | ".join(fn(models[n]) for n in names) + " |")

    for k in gate_keys:
        row(f"Gate: {k}", lambda m, k=k: "PASS" if m["gates"][k] else "FAIL")
    row("All gates", lambda m: "PASS" if m["passes_all_gates"] else "FAIL")
    row("Cases run", lambda m: str(m["cases"]))
    row("Schema valid first try", lambda m: f"{m['schema_first_try']:.0%}")
    row("Schema valid after retry", lambda m: f"{m['schema_after_retry']:.0%}")
    row("Invalid citations", lambda m: str(m["invalid_citations"]))
    row("Fabricated facts", lambda m: str(m["fabricated_facts"]))
    row("Clinical claims", lambda m: str(m["clinical_claims"]))
    row("Leaks", lambda m: str(m["leaks"]))
    row("Injection echoes", lambda m: str(m["injection_echoes"]))
    row("Miscited statements", lambda m: f"{m['miscited_rate']:.1%}")
    row("Unsupported suggestions (minor)", lambda m: str(m["minor_unsupported_suggestions"]))
    row("Failure rate", lambda m: f"{m['failure_rate']:.1%}")
    row(
        "Latency p50 / p95 / mean (s)",
        lambda m: f"{m['p50_s']:.0f} / {m['p95_s']:.0f} / {m['mean_s']:.0f}",
    )
    row("Generation tokens/s", lambda m: f"{m['gen_tokens_per_s']:.1f}")
    row("Loaded model GiB (in GPU)", lambda m: f"{m['peak_model_gib']:.1f} ({m['vram_gib']:.1f})")
    row("Peak machine memory GiB", lambda m: f"{m['peak_system_gib']:.1f}")
    row("Retries used", lambda m: str(m["retries_used"]))
    row("Rubric (template " + f"{template_rubric:.3f})", lambda m: f"{m['rubric']:.3f}")
    for part in ("coverage", "barrier", "conflict", "missing"):
        row(f"  rubric: {part}", lambda m, p=part: f"{m['rubric_parts'][p]:.2f}")

    lines += [
        "",
        f"**Winner:** {winner or 'none'} ({why.rstrip('.')}).",
        "",
        "## Faults to read by hand",
        "",
    ]
    for model, recs in raw.items():
        ex = examples(recs)
        lines.append(f"### {model}")
        lines += [f"- {e}" for e in ex] or ["- none"]
        lines.append("")
    (run_dir / "summary.md").write_text("\n".join(lines))
    return summary


if __name__ == "__main__":
    write(Path(sys.argv[1]))
