"""Run the Case Brief evaluation against local Ollama models.

    python -m evals.case_brief.run                       # all three models, 30 cases
    python -m evals.case_brief.run --models qwen3.5:9b-q4_K_M --cases normal-01 sparse-16
    python -m evals.case_brief.run --resume 20261014-1530   # skip finished cases
    python -m evals.case_brief.run --set holdout --resume holdout-v7   # the 10 holdout cases

Results go to evals/case_brief/results/<run id>/ (git-ignored): one JSON per model and case,
baseline.json, meta.json, summary.md and summary.json.
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from . import gate, ollama, prompt, report, thresholds
from .baseline import template_brief
from .cases import build_cases
from .checks import evaluate
from .holdout import build_holdout
from .holdout2 import build_holdout2

DEFAULT_MODELS = ["qwen3.5:9b-q4_K_M", "granite4.1:8b-q4_K_M", "qwen3.5:4b-q4_K_M"]
RESULTS = Path(__file__).parent / "results"


def _parse(text: str) -> tuple[object | None, str | None]:
    try:
        return prompt.OUTPUT_MODEL.model_validate_json(text), None
    except (ValidationError, ValueError) as exc:
        return None, str(exc)[:600]


def run_case(
    host: str, model: str, case, timeout: float, temperature: float, seed: int, label: str
) -> dict:
    schema = prompt.output_schema(case)
    msgs = prompt.messages(case)
    rec: dict = {
        "model": f"{model} {label}".strip(),
        "case": case.id,
        "category": case.category,
        "attempts": [],
    }
    brief = None
    t0 = time.perf_counter()
    with ollama.PeakMemory() as mem:
        try:
            for _attempt in range(2):
                out = ollama.chat(host, model, msgs, schema, timeout, temperature, seed)
                text = out["message"]["content"]
                brief, err = _parse(text)
                rec["attempts"].append(
                    {
                        "output": text,
                        "error": err,
                        "prompt_tokens": out.get("prompt_eval_count", 0),
                        "prompt_ns": out.get("prompt_eval_duration", 0),
                        "gen_tokens": out.get("eval_count", 0),
                        "gen_ns": out.get("eval_duration", 0),
                        "load_ns": out.get("load_duration", 0),
                    }
                )
                if brief:
                    break
                msgs = msgs + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": f"That was invalid: {err}\nReturn corrected JSON only.",
                    },
                ]
        except Exception as exc:  # noqa: BLE001 - any runtime failure counts as a failure
            rec["exception"] = f"{type(exc).__name__}: {exc}"[:300]
    rec["seconds"] = round(time.perf_counter() - t0, 2)
    rec["peak_system_gib"] = round(mem.peak, 2) if mem.peak is not None else None
    ps = [m for m in ollama.loaded(host) if m["name"] == model]
    rec["model_gib"] = round(ps[0]["size"] / 2**30, 2) if ps else None
    rec["vram_gib"] = round(ps[0].get("size_vram", 0) / 2**30, 2) if ps else None
    rec["first_try_valid"] = bool(rec["attempts"]) and rec["attempts"][0]["error"] is None
    rec["valid"] = brief is not None
    rec["failure"] = brief is None
    final = rec["attempts"][-1]["output"] if rec["attempts"] and brief else None
    rec["evaluation"], decision = gate.assess(case, final, int(prompt.PROMPT_VERSION))
    rec["gate"] = {"source": decision["source"], "reasons": decision["reasons"]}
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--cases", nargs="+", help="case ids to run (default: all 30)")
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--resume", help="run id to continue")
    ap.add_argument(
        "--set",
        choices=["dev", "holdout", "holdout2"],
        default="dev",
        help="case set (holdout: never tune)",
    )
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--label", default="", help="suffix for repeat runs, e.g. r1")
    args = ap.parse_args()

    ollama.guard(args.host)
    for m in args.models:
        ollama.guard(args.host, m)
    have = ollama.installed(args.host)
    missing = [m for m in args.models if m not in have]
    if missing:
        raise SystemExit(f"Not installed locally (pull them first): {missing}")

    pool = {"holdout": build_holdout, "holdout2": build_holdout2}.get(args.set, build_cases)()
    cases = [c for c in pool if not args.cases or c.id in args.cases]
    run_id = args.resume or datetime.now().strftime("%Y%m%d-%H%M")
    out_dir = RESULTS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    seen, settings = [], {}
    if (out_dir / "meta.json").exists():
        old = json.loads((out_dir / "meta.json").read_text())
        seen, settings = old["models"], old.get("settings", {})
    for m in args.models:
        settings[f"{m} {args.label}".strip()] = {"temperature": args.temperature, "seed": args.seed}
    (out_dir / "meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "ollama_version": ollama.version(args.host),
                "prompt_version": prompt.PROMPT_VERSION,
                "models": seen + [m for m in args.models if m not in seen],
                "cases": len(cases),
                "case_set": args.set,
                "model_digests": {
                    m["name"]: m["digest"]
                    for m in ollama.tags(args.host)
                    if m["name"] in args.models
                },
                "settings": settings,
                "thresholds": {k: v for k, v in vars(thresholds).items() if k.isupper()},
                "idle_system_gib": ollama.system_used_gib(),
            },
            indent=2,
        )
    )

    base = [
        {"case": c.id, "evaluation": evaluate(c, template_brief(c), **prompt.VIEW)} for c in cases
    ]
    (out_dir / "baseline.json").write_text(json.dumps(base, indent=2))

    for model in args.models:
        mdir = out_dir / f"{model} {args.label}".strip().replace(":", "_").replace(" ", "_")
        mdir.mkdir(exist_ok=True)
        ollama.unload_all(args.host)
        print(f"\n== {model}: warm-up", flush=True)
        ollama.chat(
            args.host, model, [{"role": "user", "content": "Say OK."}], {"type": "object"}, 600
        )
        for i, case in enumerate(cases, 1):
            path = mdir / f"{case.id}.json"
            if path.exists():
                continue
            rec = run_case(
                args.host, model, case, args.timeout, args.temperature, args.seed, args.label
            )
            path.write_text(json.dumps(rec, indent=2))
            crit = len(rec["evaluation"]["critical"]) if rec["evaluation"] else "n/a"
            print(
                f"[{i:02d}/{len(cases)}] {case.id:<12} {rec['seconds']:>6.1f}s "
                f"valid={rec['valid']} critical={crit} shown={rec['gate']['source']}",
                flush=True,
            )

    report.write(out_dir)
    print(f"\nResults: {out_dir}")


if __name__ == "__main__":
    main()
