// AI context evaluation (administrator only), a section of the AI context diagnostics page.
// Runs the server's predefined synthetic scenarios through the real shared context services and
// shows the readiness verdict, the confidence index and how it was calculated, every scenario
// with its expected and actual result, an inference trace per pipeline scenario, the prompt
// templates and configuration in force, and the findings register. The page sends no input:
// the server decides what runs. Nothing here registers, enables or calls an AI artifact.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, post } from "../api";
import type { Json } from "../api";
import { Alert, Badge, Button, Card, ErrorNote, LoadingRows, Select, Table, cx } from "../ui";
import type { Tone } from "../ui";

const label = (s: string) => s.replace(/_/g, " ");

const READINESS: Record<string, { text: string; tone: Tone }> = {
  not_evaluated: { text: "Not evaluated", tone: "neutral" },
  blocked: { text: "Blocked", tone: "bad" },
  insufficient_evidence: { text: "Insufficient evidence", tone: "warn" },
  below_threshold: { text: "Below threshold", tone: "warn" },
  eligible_for_owner_review: { text: "Eligible for owner review", tone: "ok" },
};

const STATUS_TONE: Record<string, Tone> = {
  pass: "ok",
  fail: "bad",
  not_applicable: "neutral",
  verified: "ok",
  partial: "warn",
  failed: "bad",
  unverified: "warn",
  verified_closed: "ok",
  partially_resolved: "warn",
  external_verification_required: "warn",
  blocked_by_owner_decision: "info",
  out_of_scope: "neutral",
  accepted_limitation: "neutral",
  open_regressed: "bad",
  open: "bad",
};

function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{label(status)}</Badge>;
}

export function ReadinessBadge({ readiness }: { readiness: string }) {
  const r = READINESS[readiness] ?? READINESS.not_evaluated;
  return <Badge tone={r.tone}>{r.text}</Badge>;
}

function download(data: unknown, name: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function Overview({ result, exported }: { result: Json; exported: Json }) {
  const s = result.score;
  const c = result.counts;
  return (
    <Card
      title="Evaluation overview"
      description={`Run ${result.run.at} · code revision ${result.run.revision ?? "unknown"} (uncommitted changes not detected) · ${result.run.rubric_version} · ${result.run.duration_ms} ms`}
      action={<Button onClick={() => download(exported, "ai-context-evaluation.json")}>Export sanitized report</Button>}
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <div className="text-xs text-ink-subtle">General-purpose context reliability confidence</div>
          <div className="text-3xl font-semibold" aria-label={`Confidence ${s.confidence} of 100`}>
            {s.confidence}
            <span className="text-base font-normal text-ink-subtle"> / 100 (threshold {s.threshold})</span>
          </div>
          <div className="mt-1 text-xs text-ink-subtle">A weighted evidence index over fixed controls. Not a probability and not the pass rate.</div>
        </div>
        <div>
          <div className="text-xs text-ink-subtle">Readiness</div>
          <div className="mt-1">
            <ReadinessBadge readiness={s.readiness} />
          </div>
          <div className="mt-1 text-xs text-ink-subtle">Even when eligible, nothing is enabled: each future consumer needs owner approval, registration and its own tests.</div>
        </div>
        <div>
          <div className="text-xs text-ink-subtle">Scenarios (test pass rate, reported separately)</div>
          <div className="mt-1 text-sm">
            {c.executed} executed · {c.passed} passed · {c.failed} failed · {c.skipped} skipped · {c.not_applicable} not applicable
          </div>
          <div className="text-sm">Pass rate {result.pass_rate}%</div>
          <div className="text-xs text-ink-subtle">{result.run.worlds}. Model calls: {result.run.model_calls}.</div>
        </div>
      </div>
      {s.hard_blockers.length > 0 && (
        <Alert tone="bad" className="mt-4">
          Hard blockers: {s.hard_blockers.map((b: Json) => b.label).join("; ")}. Readiness is blocked whatever the score.
        </Alert>
      )}
      {s.open_high_findings.length > 0 && (
        <Alert tone="bad" className="mt-4">
          Open Critical or High findings: {s.open_high_findings.join(", ")}.
        </Alert>
      )}
      {result.external.length > 0 && (
        <div className="mt-4">
          <h3 className="mb-1 text-sm font-semibold">Controls without runtime evidence (earn no credit)</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {result.external.map((e: Json) => (
              <li key={e.control}>
                <span className="font-semibold">{e.name}</span>
                {e.note ? <span className="text-ink-subtle"> — {e.note}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function Dimensions({ result }: { result: Json }) {
  const [open, setOpen] = useState(false);
  return (
    <Card title="Coverage and confidence by dimension" description="Weights were fixed before any result was read. A control earns credit only from scenarios that ran here.">
      <Table head={["Dimension", "Weight", "Score", "Points", "Controls evidenced"]} align={[undefined, "right", "right", "right", "right"]}>
        {result.score.dimensions.map((d: Json) => (
          <tr key={d.id} className="border-t border-line align-top">
            <td className="py-1.5 pr-3">
              <div className="font-semibold">{d.name}</div>
              <div className="text-xs text-ink-subtle">{d.why}</div>
            </td>
            <td className="py-1.5 pr-3 text-right">{d.weight}</td>
            <td className="py-1.5 pr-3 text-right">{d.score}%</td>
            <td className="py-1.5 pr-3 text-right">{d.points}</td>
            <td className="py-1.5 text-right">
              {d.evidenced} / {d.controls}
            </td>
          </tr>
        ))}
      </Table>
      <div className="mt-3">
        <Button size="sm" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? "Hide controls" : "Show every control"}
        </Button>
      </div>
      {open && (
        <div className="mt-3">
          <Table head={["Control", "Dimension", "Kind", "Status", "Credit", "Scenarios"]}>
            {result.score.controls.map((c: Json) => (
              <tr key={c.id} className="border-t border-line align-top">
                <td className="py-1.5 pr-3">
                  <div className="font-mono text-xs">{c.id}</div>
                  <div className="text-xs">{c.name}</div>
                </td>
                <td className="py-1.5 pr-3 text-xs">{label(c.dimension)}</td>
                <td className="py-1.5 pr-3 text-xs">{c.kind}</td>
                <td className="py-1.5 pr-3">
                  <StatusBadge status={c.status} />
                </td>
                <td className="py-1.5 pr-3 text-xs">{c.credit}</td>
                <td className="py-1.5 text-xs">{c.scenarios}</td>
              </tr>
            ))}
          </Table>
        </div>
      )}
    </Card>
  );
}

function Comparison({ result }: { result: Json }) {
  const cmp = result.comparison;
  if (!cmp.previous) {
    return (
      <Card title="Comparison with the previous run">
        <p className="text-sm text-ink-subtle">No earlier evaluation is recorded.</p>
      </Card>
    );
  }
  return (
    <Card
      title="Comparison with the previous run"
      description={`Previous: ${cmp.previous.at} · confidence ${cmp.previous.confidence} · ${label(String(cmp.previous.readiness))} · revision ${cmp.previous.revision ?? "unknown"}`}
    >
      {cmp.changes.length === 0 ? (
        <p className="text-sm">No scenario changed result.</p>
      ) : (
        <Table head={["Scenario", "Change", "Related finding", "Reproducing tests"]}>
          {cmp.changes.map((ch: Json) => (
            <tr key={ch.id} className="border-t border-line align-top">
              <td className="py-1.5 pr-3 font-mono text-xs">{ch.id}</td>
              <td className="py-1.5 pr-3">
                <Badge tone={ch.kind === "regression" ? "bad" : "ok"}>
                  {label(ch.from)} → {label(ch.to)}
                </Badge>
              </td>
              <td className="py-1.5 pr-3 text-xs">{ch.finding ?? "none"}</td>
              <td className="py-1.5 text-xs">{ch.refs.join(", ")}</td>
            </tr>
          ))}
        </Table>
      )}
      <p className="mt-2 text-xs text-ink-subtle">Only the scenario's own finding and tests are named; no other cause of a change is claimed.</p>
    </Card>
  );
}

function Scenarios({ result, onTrace }: { result: Json; onTrace: (id: string) => void }) {
  const [status, setStatus] = useState("all");
  const [dimension, setDimension] = useState("all");
  const previous = result.comparison.previous ? (result.comparison.changes as Json[]) : [];
  const prevOf = (id: string) => previous.find((c: Json) => c.id === id)?.from;
  const rows = useMemo(
    () =>
      (result.scenarios as Json[]).filter(
        (r) => (status === "all" || r.status === status) && (dimension === "all" || r.dimension === dimension),
      ),
    [result, status, dimension],
  );
  return (
    <Card title="Test scenarios and results" description="Every scenario ran the real shared services on the synthetic fixture worlds. Expected results come from Master_Build, independent calculation or the documented contract.">
      <div className="mb-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Select label="Result" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="all">All results</option>
          <option value="pass">Passed</option>
          <option value="fail">Failed</option>
          <option value="not_applicable">Not applicable</option>
        </Select>
        <Select label="Dimension" value={dimension} onChange={(e) => setDimension(e.target.value)}>
          <option value="all">All dimensions</option>
          {result.score.dimensions.map((d: Json) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </Select>
      </div>
      <p className="mb-2 text-xs text-ink-subtle" aria-live="polite">
        Showing {rows.length} of {result.scenarios.length} scenarios.
      </p>
      <Table head={["Scenario", "Domain / role", "Expected", "Actual", "Result", "Gate", "Finding", ""]}>
        {rows.map((r: Json) => (
          <tr key={r.id} className="border-t border-line align-top">
            <td className="py-1.5 pr-3">
              <div className="font-mono text-xs">{r.id}</div>
              <div className="text-xs">{r.name}</div>
              <div className="text-[11px] text-ink-subtle">{r.refs.join(" · ")}</div>
            </td>
            <td className="py-1.5 pr-3 text-xs">
              {r.domain}
              {r.role ? <div className="text-ink-subtle">{r.role}</div> : null}
            </td>
            <td className="py-1.5 pr-3 text-xs">{r.expected}</td>
            <td className="py-1.5 pr-3 text-xs">{r.actual}</td>
            <td className="py-1.5 pr-3">
              <StatusBadge status={r.status} />
              {prevOf(r.id) && <div className="mt-1 text-[11px] text-ink-subtle">was {label(prevOf(r.id))}</div>}
              {r.hard_blocker && <div className="mt-1 text-[11px] text-ink-subtle">hard blocker if failed</div>}
            </td>
            <td className="py-1.5 pr-3 text-xs">{r.gate}</td>
            <td className="py-1.5 pr-3 text-xs">{r.finding ?? ""}</td>
            <td className="py-1.5">
              {r.trace && (
                <Button size="sm" onClick={() => onTrace(r.id)} aria-label={`Show the inference trace of ${r.id}`}>
                  Trace
                </Button>
              )}
            </td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function Section({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-line pt-3" aria-label={title}>
      <h3 className="mb-1 text-sm font-semibold">
        {n}. {title}
      </h3>
      {children}
    </section>
  );
}

function Messages({ messages }: { messages: Json[] }) {
  if (!messages.length) return <p className="text-xs text-ink-subtle">None.</p>;
  return (
    <div className="space-y-2">
      {messages.map((m: Json, i: number) => (
        <div key={i}>
          <div className="text-[11px] font-semibold uppercase text-ink-subtle">{m.role}</div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-subtle p-2 text-xs">{m.content}</pre>
        </div>
      ))}
    </div>
  );
}

export function Trace({ id, trace, onClose }: { id: string; trace: Json; onClose: () => void }) {
  const [view, setView] = useState<"redacted" | "sent">("redacted");
  const step = (name: string) => (trace.steps as Json[]).filter((s) => s.step === name);
  const p = trace.prompt;
  const sentMessages = p.sent ? p.payload_sent : p.payload_built_not_sent;
  return (
    <Card
      title={`Inference trace: ${id}`}
      description="Operational evidence of each pipeline step. No model reasoning is shown; the model is a scripted test double and the data is synthetic."
      action={<Button onClick={onClose}>Close trace</Button>}
    >
      <div className="space-y-3 text-sm">
        <Section n={1} title="Consumer contract and requested categories">
          <p className="text-xs">
            {trace.contract.operation} v{trace.contract.version} {trace.contract.test_only ? "(test-only, never registered)" : ""} · subject {trace.contract.subject_type} ·
            permission {trace.contract.permission} · categories {trace.contract.categories.join(", ")} · partial allowed {String(trace.contract.allow_partial)} · freshness limit{" "}
            {trace.contract.max_age_days ?? "none"} days
          </p>
        </Section>
        <Section n={2} title="Role and effective scope">
          <p className="text-xs">
            {label(trace.principal.role)} ({trace.principal.account}) · authorisation:{" "}
            {step("authorisation")
              .map((s) => label(s.outcome))
              .join(", ") || "not reached"}
          </p>
        </Section>
        <Section n={3} title="Source and provenance">
          <p className="text-xs">
            {trace.source.id} via {trace.source.read_services.join(", ")} · policy {trace.source.policy_version ?? "—"} · fingerprint{" "}
            {trace.source.fingerprint ? `${String(trace.source.fingerprint).slice(0, 12)}…` : "—"} · {trace.source.principal_dependent ? "reader-dependent view" : "same for every permitted reader"}
          </p>
        </Section>
        <Section n={4} title="Category states">
          <div className="flex flex-wrap gap-2">
            {trace.policy.map((c: Json) => (
              <Badge key={c.category} tone={c.state === "included" ? "ok" : c.state === "verified_empty" ? "info" : ["stale", "truncated", "excluded_policy"].includes(c.state) ? "warn" : c.state === "unavailable" ? "bad" : "neutral"}>
                {c.category}: {c.state ? label(c.state) : "not built"}
                {c.reason ? ` (${label(c.reason)})` : ""}
              </Badge>
            ))}
          </div>
        </Section>
        <Section n={5} title="Global and consumer policy decisions">
          <Table head={["Category", "Model-eligible (global policy)", "In consumer allowlist"]}>
            {trace.policy.map((c: Json) => (
              <tr key={c.category} className="border-t border-line">
                <td className="py-1 pr-3 text-xs">{c.category}</td>
                <td className="py-1 pr-3 text-xs">{c.model_eligible ? "yes" : "no (server text only)"}</td>
                <td className="py-1 text-xs">{c.in_allowlist ? "yes" : "no"}</td>
              </tr>
            ))}
          </Table>
        </Section>
        <Section n={6} title="Prompt template and payload">
          <p className="mb-2 text-xs">
            Template {p.template_version} ·{" "}
            {p.sent ? <Badge tone="info">sent to the provider</Badge> : <Badge tone="warn">not sent</Badge>} · canaries in payload {p.canaries_in_payload} · identifiers in payload{" "}
            {p.identifiers_in_payload}
          </p>
          <div role="radiogroup" aria-label="Payload view" className="mb-2 flex gap-2">
            {(["redacted", "sent"] as const).map((v) => (
              <button
                key={v}
                type="button"
                role="radio"
                aria-checked={view === v}
                onClick={() => setView(v)}
                className={cx("min-h-8 rounded-lg px-3 text-xs font-semibold ring-1 ring-inset", view === v ? "bg-ink text-surface ring-ink" : "bg-surface ring-line-strong")}
              >
                {v === "redacted" ? "Redacted diagnostic preview" : p.sent ? "Actual payload sent" : "Payload built (refused, never sent)"}
              </button>
            ))}
          </div>
          <Messages messages={view === "redacted" ? p.payload_redacted : sentMessages} />
        </Section>
        <Section n={7} title="Evidence supplied to the model">
          <Table head={["Evidence id", "Category", "Source", "Version", "Dated"]}>
            {trace.source.evidence.map((e: Json) => (
              <tr key={e.id} className="border-t border-line">
                <td className="py-1 pr-3 font-mono text-xs">{e.id}</td>
                <td className="py-1 pr-3 text-xs">{e.category}</td>
                <td className="py-1 pr-3 text-xs">{e.source}</td>
                <td className="py-1 pr-3 text-xs">{e.source_version ?? ""}</td>
                <td className="py-1 text-xs">{e.dated ?? ""}</td>
              </tr>
            ))}
          </Table>
        </Section>
        <Section n={8} title="Output schema and validation rules">
          {trace.output_contract.error ? (
            <Alert tone="bad">Refused output contract: {trace.output_contract.error}</Alert>
          ) : (
            <p className="text-xs">
              {Object.entries(trace.output_contract.paths as Record<string, string>)
                .map(([path, kind]) => `${path}: ${kind}`)
                .join(" · ")}
            </p>
          )}
        </Section>
        <Section n={9} title="Safety checks and outcomes">
          <p className="text-xs">Shared checks: {trace.checks.shared.join(", ")}.</p>
          <p className="text-xs">
            Findings: {trace.checks.findings.length ? trace.checks.findings.map((f: Json) => `${f.kind} at ${f.where}`).join("; ") : "none"}
          </p>
        </Section>
        <Section n={10} title="Provider mode and fallback reason">
          <p className="text-xs">
            Mode {step("mode")[0]?.outcome ?? "not reached"} · provider {trace.provider.name} ({trace.provider.local ? "local" : "not local"}) · calls {trace.provider.calls} · {trace.provider.note}
          </p>
        </Section>
        <Section n={11} title="Final disposition">
          <p className="text-xs">
            <StatusBadge status={trace.disposition.status === "completed" ? "pass" : "partial"} /> {label(trace.disposition.status)}
            {trace.disposition.failure ? ` (${label(trace.disposition.failure)})` : ""} · output stored: {trace.disposition.output_stored ? "yes" : "no"}
          </p>
        </Section>
      </div>
    </Card>
  );
}

function Transparency({ result }: { result: Json }) {
  const t = result.transparency;
  return (
    <>
      <Card title="Prompts and configuration in force" description="Templates and settings the evaluation ran under. No secret or environment value is shown.">
        <div className="space-y-3 text-sm">
          {t.operations.map((o: Json) => (
            <div key={o.id}>
              <div className="font-semibold">
                {o.id} v{o.version} · prompt {o.prompt_version} · {o.subject_type}
              </div>
              <div className="text-xs text-ink-subtle">
                Cited text: {o.cited_fields.map((c: string[]) => `${c[0]} (citations ${c[1]})`).join(", ") || "none"} · uncited text: {o.text_fields.join(", ") || "none"}
              </div>
              {o.system_prompt && <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-subtle p-2 text-xs">{o.system_prompt}</pre>}
            </div>
          ))}
          <div>
            <div className="font-semibold">Test-only probe prompt {t.probe_prompt.version}</div>
            <pre className="mt-1 whitespace-pre-wrap rounded-lg bg-subtle p-2 text-xs">{t.probe_prompt.system}</pre>
          </div>
          <p className="text-xs">
            Provider {t.config.provider} · modes {JSON.stringify(t.config.operation_modes)} · disabled {t.config.operations_disabled.join(", ") || "none"} · policy{" "}
            {t.config.policy_version} · {t.config.rubric_version}
          </p>
        </div>
      </Card>
      <Card title="Findings register" description="Status derives from the scenarios that reproduce a finding when it has any; otherwise from the recorded evidence.">
        <Table head={["ID", "Severity", "Finding", "Status", "Scenarios", "Evidence"]}>
          {result.findings.map((f: Json) => (
            <tr key={f.id} className="border-t border-line align-top">
              <td className="py-1.5 pr-3 font-mono text-xs">{f.id}</td>
              <td className="py-1.5 pr-3 text-xs">{f.severity}</td>
              <td className="py-1.5 pr-3 text-xs">{f.title}</td>
              <td className="py-1.5 pr-3">
                <StatusBadge status={f.status} />
              </td>
              <td className="py-1.5 pr-3 text-xs">{f.scenarios.length ? `${f.scenarios_passed} / ${f.scenarios.length} pass` : "none"}</td>
              <td className="py-1.5 text-xs">{f.evidence}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </>
  );
}

export default function AiEvaluation() {
  const last = useQuery({ queryKey: ["ai-diag", "evaluation"], queryFn: () => api("/ai/diagnostics/evaluation") });
  const run = useMutation({ mutationFn: () => post("/ai/diagnostics/evaluation/run") });
  const [traceId, setTraceId] = useState<string | null>(null);
  const body = run.data as Json | undefined;
  const result = body?.result as Json | undefined;
  const previous = last.data as Json | undefined;
  return (
    <div className="space-y-4">
      <Card
        title="AI context evaluation"
        description="Assesses whether the shared context layer can safely serve an unseen, policy-approved AI consumer. Predefined synthetic scenarios only, on isolated fixture worlds; no input is accepted, no model is called, and no AI artifact is registered or enabled."
      >
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="primary" busy={run.isPending} onClick={() => run.mutate()}>
            Run evaluation
          </Button>
          {!result &&
            (last.isLoading ? (
              <LoadingRows rows={1} />
            ) : last.error ? (
              <ErrorNote error={last.error} />
            ) : previous?.last ? (
              <span className="text-sm">
                Last run {previous.last.at}: confidence {previous.last.confidence} · <ReadinessBadge readiness={previous.readiness} /> · {previous.last.counts?.passed} of{" "}
                {previous.last.counts?.executed} scenarios passed
              </span>
            ) : (
              <span className="text-sm">
                <ReadinessBadge readiness="not_evaluated" /> No evaluation recorded yet.
              </span>
            ))}
        </div>
        <div aria-live="polite" className="sr-only">
          {run.isPending ? "Evaluation running" : result ? `Evaluation finished: ${READINESS[result.score.readiness]?.text}` : ""}
        </div>
        {run.error && <ErrorNote error={run.error} />}
      </Card>
      {result && (
        <>
          <Overview result={result} exported={body!.export} />
          {traceId && result.traces[traceId] && <Trace id={traceId} trace={result.traces[traceId]} onClose={() => setTraceId(null)} />}
          <Dimensions result={result} />
          <Comparison result={result} />
          <Scenarios result={result} onTrace={setTraceId} />
          <Transparency result={result} />
        </>
      )}
    </div>
  );
}
