// AI context diagnostics (administrator only). Inspects the shared AI context layer: registered
// sources, the field policy, context builds with every decision explained, the shared safety
// checks, multi-subject and aggregate contexts, health and a sanitized report. Everything runs
// on the server through the real context code; this page only shows the answers. It never calls
// a model and never changes a record. Access is decided by the API (`ai:diagnostics`).
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, post } from "../api";
import type { Json } from "../api";
import { Alert, Badge, Button, Card, ErrorNote, ErrorState, LoadingRows, PageHeader, Select, Table, TextArea, cx } from "../ui";
import type { Tone } from "../ui";

type Tab = "sources" | "context" | "safety" | "aggregate" | "health" | "report";

const TABS: Array<{ value: Tab; label: string }> = [
  { value: "sources", label: "Sources and policy" },
  { value: "context", label: "Context playground" },
  { value: "safety", label: "Safety checks" },
  { value: "aggregate", label: "Multi-subject" },
  { value: "health", label: "Health" },
  { value: "report", label: "Report" },
];

const SUBJECTS = [
  "patient", "hcp", "recommendation", "content", "care_request", "patient_self", "care_queue",
  "consultation_queue", "care_workflow", "content_lineage", "ai_telemetry", "audit_activity",
  "job_queue", "quality_measures", "access_scope",
];
const ROLES = ["care_manager", "medical_rep", "hcp", "patient", "compliance", "admin"];

const STATE_TONE: Record<string, Tone> = {
  included: "ok",
  verified_empty: "info",
  not_requested: "neutral",
  excluded_policy: "warn",
  excluded_allowlist: "neutral",
  unavailable: "bad",
  truncated: "warn",
  stale: "warn",
};

const label = (s: string) => s.replace(/_/g, " ");

function ResultBadge({ result }: { result: string }) {
  const tone: Tone = result === "pass" ? "ok" : result === "fail" ? "bad" : "neutral";
  return <Badge tone={tone}>{result}</Badge>;
}

function Sources() {
  const sources = useQuery({ queryKey: ["ai-diag", "sources"], queryFn: () => api("/ai/diagnostics/sources") });
  const policy = useQuery({ queryKey: ["ai-diag", "policy"], queryFn: () => api("/ai/diagnostics/policy") });
  const [record, setRecord] = useState("");
  if (sources.isLoading) return <LoadingRows />;
  if (sources.error) return <ErrorState error={sources.error} retry={() => void sources.refetch()} />;
  const data = sources.data as Json;
  const records = Object.keys(policy.data?.records ?? {});
  const shown = record || records[0] || "";
  return (
    <div className="space-y-4">
      <Card
        title="A. Context sources"
        description={`Policy ${data.policy_version} · contract ${data.contract_version}. A source grants no access: its read services decide.`}
      >
        <Table head={["Source", "Status", "Availability", "Subject types", "Categories (model-visible marked)"]}>
          {data.sources.map((s: Json) => (
            <tr key={s.id} className="border-t border-line align-top">
              <td className="py-2 pr-3">
                <div className="font-semibold">{s.id}</div>
                <div className="text-xs text-ink-subtle">{s.domain}</div>
              </td>
              <td className="py-2 pr-3">
                <Badge tone={s.status === "implemented" ? "ok" : s.status === "policy_blocked" ? "warn" : "neutral"}>{label(s.status)}</Badge>
                {s.status_reason && <div className="mt-1 text-xs text-ink-subtle">{label(s.status_reason)}</div>}
              </td>
              <td className="py-2 pr-3">{label(s.availability)}</td>
              <td className="py-2 pr-3 text-xs">{s.subject_types.map((t: Json) => `${t.type} (${t.scope})`).join(", ") || "none"}</td>
              <td className="py-2 text-xs">
                {s.categories.map((c: Json) => (
                  <span key={c.name} className={cx("mr-2 inline-block", c.model ? "font-semibold" : "text-ink-subtle")}>
                    {c.name}
                    {c.model ? "" : " (server text only)"}
                  </span>
                ))}
              </td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Registered operations" description="Consumers of the layer. Registration alone reads nothing.">
        <Table head={["Operation", "Version", "Subject", "Allowlist", "Permission"]}>
          {data.operations.map((o: Json) => (
            <tr key={o.id} className="border-t border-line">
              <td className="py-2 pr-3 font-semibold">{o.id}</td>
              <td className="py-2 pr-3">{o.version}</td>
              <td className="py-2 pr-3">{o.subject_type}</td>
              <td className="py-2 pr-3 text-xs">{o.categories.join(", ")}</td>
              <td className="py-2 text-xs">{o.permission}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Field policy" description="Every field of every record type a source reads, and where it may go.">
        {policy.isLoading ? (
          <LoadingRows rows={3} />
        ) : policy.error ? (
          <ErrorNote error={policy.error} />
        ) : (
          <>
            <Select label="Record type" value={shown} onChange={(e) => setRecord(e.target.value)} className="mb-3 max-w-xs">
              {records.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </Select>
            <Table head={["Field", "Disposition"]}>
              {Object.entries((policy.data?.records ?? {})[shown] ?? {}).map(([field, rule]) => (
                <tr key={field} className="border-t border-line">
                  <td className="py-1.5 pr-3 font-mono text-xs">{field}</td>
                  <td className="py-1.5">
                    <Badge tone={String(rule).startsWith("model") ? "ok" : String(rule).startsWith("@") ? "info" : "neutral"}>{label(String(rule))}</Badge>
                  </td>
                </tr>
              ))}
            </Table>
          </>
        )}
      </Card>
    </div>
  );
}

function ContextPlayground() {
  const [mode, setMode] = useState("fixture");
  const [subject, setSubject] = useState("patient");
  const [role, setRole] = useState("care_manager");
  const [which, setWhich] = useState("auto");
  const [recordId, setRecordId] = useState("");
  const run = useMutation({
    mutationFn: () =>
      post("/ai/diagnostics/context", {
        mode,
        subject_type: subject,
        ...(mode === "fixture" ? { role, subject: which } : recordId ? { subject: recordId } : {}),
      }),
  });
  const result = run.data as Json | undefined;
  return (
    <div className="space-y-4">
      <Card
        title="B. Context request playground"
        description="Builds a context with the real engine. Fixture mode uses the synthetic fixture world as its own synthetic account of the chosen role. Record preview reads this database with your own access and shows no values."
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Select label="Mode" value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="fixture">Synthetic fixtures</option>
            <option value="record_preview">Redacted record preview</option>
          </Select>
          <Select label="Subject type" value={subject} onChange={(e) => setSubject(e.target.value)}>
            {SUBJECTS.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </Select>
          {mode === "fixture" ? (
            <>
              <Select label="Synthetic role" value={role} onChange={(e) => setRole(e.target.value)}>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {label(r)}
                  </option>
                ))}
              </Select>
              <Select label="Subject" value={which} onChange={(e) => setWhich(e.target.value)}>
                <option value="auto">In scope (expected)</option>
                <option value="out_of_scope">Out of scope (expected denial)</option>
              </Select>
            </>
          ) : (
            <label className="text-sm">
              <span className="sr-only">Record id</span>
              <input
                aria-label="Record id"
                value={recordId}
                onChange={(e) => setRecordId(e.target.value)}
                placeholder="Record id (for record subjects)"
                className="h-10 w-full rounded-lg border border-line-strong bg-surface px-3"
                maxLength={32}
              />
            </label>
          )}
        </div>
        <div className="mt-3">
          <Button variant="primary" busy={run.isPending} onClick={() => run.mutate()}>
            Build context
          </Button>
        </div>
        {run.error && <ErrorNote error={run.error} />}
      </Card>
      {result && <ContextResult result={result} />}
    </div>
  );
}

function ContextResult({ result }: { result: Json }) {
  const ctx = result.context;
  return (
    <>
      <Card title="C. Authorization and policy decisions" description={`Principal: ${result.principal}`}>
        <Table head={["Check", "Result", "Code"]}>
          {result.checks.map((c: Json) => (
            <tr key={c.check} className="border-t border-line">
              <td className="py-1.5 pr-3">{label(c.check)}</td>
              <td className="py-1.5 pr-3">
                <ResultBadge result={c.result} />
              </td>
              <td className="py-1.5 text-xs">{c.code ? label(c.code) : ""}</td>
            </tr>
          ))}
        </Table>
      </Card>
      {ctx && (
        <Card
          title="D. Context preview and provenance"
          description={`Source ${ctx.source} · as of ${ctx.as_of} · policy ${ctx.policy_version} · fingerprint ${String(ctx.fingerprint).slice(0, 12)}…`}
          action={ctx.redacted ? <Badge tone="warn">Redacted preview: differs from the model payload</Badge> : <Badge tone="info">Synthetic values</Badge>}
        >
          <div className="mb-3 flex flex-wrap gap-2 text-xs">
            <Badge tone={ctx.complete ? "ok" : "warn"}>{ctx.complete ? "Complete" : "Incomplete"}</Badge>
            <Badge>{ctx.model_item_count} model-visible items</Badge>
            <Badge>{ctx.reader_note_count} reader-only notes (not shown)</Badge>
            <Badge>{ctx.withheld_count} withheld texts (not shown)</Badge>
          </div>
          <h3 className="mb-1 text-sm font-semibold">Categories</h3>
          <div className="mb-4 flex flex-wrap gap-2">
            {ctx.categories.map((c: Json) => (
              <Badge key={c.category} tone={STATE_TONE[c.state] ?? "neutral"}>
                {c.category}: {label(c.state)}
                {c.reason ? ` (${label(c.reason)})` : ""}
              </Badge>
            ))}
          </div>
          <Table head={["Item", "Category", "Source", "Recorded", "Model", "Text"]}>
            {ctx.items.map((i: Json) => (
              <tr key={i.id} className="border-t border-line align-top">
                <td className="py-1.5 pr-3 font-mono text-xs">{i.id}</td>
                <td className="py-1.5 pr-3 text-xs">{i.category}</td>
                <td className="py-1.5 pr-3 text-xs">{i.source}</td>
                <td className="py-1.5 pr-3 text-xs">{i.recorded_at ?? ""}</td>
                <td className="py-1.5 pr-3">{i.model_visible ? <Badge tone="ok">yes</Badge> : <Badge>no</Badge>}</td>
                <td className="py-1.5 text-xs">{i.text}</td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
    </>
  );
}

function Safety() {
  const [text, setText] = useState("");
  const run = useMutation({ mutationFn: () => post("/ai/diagnostics/safety", text ? { text } : {}) });
  const data = run.data as Json | undefined;
  return (
    <Card
      title="E. Safety and evidence diagnostics"
      description="Runs the shared deterministic checks against fixed adversarial cases on the fixture world. Optional text is checked against a fixture context. Nothing is sent to a model."
    >
      <TextArea label="Optional text to check" value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} rows={3} />
      <div className="mt-3">
        <Button variant="primary" busy={run.isPending} onClick={() => run.mutate()}>
          Run checks
        </Button>
      </div>
      {run.error && <ErrorNote error={run.error} />}
      {data && (
        <div className="mt-4">
          <p className="mb-2 text-sm">
            {data.passed} passed, {data.failed} failed. {data.note}
          </p>
          <Table head={["Case", "Expected", "Found", "Result"]}>
            {data.cases.map((c: Json) => (
              <tr key={c.id} className="border-t border-line align-top">
                <td className="py-1.5 pr-3">
                  <div className="font-semibold">{label(c.id)}</div>
                  <div className="text-xs text-ink-subtle">{c.description}</div>
                </td>
                <td className="py-1.5 pr-3 text-xs">{c.expected}</td>
                <td className="py-1.5 pr-3 text-xs">{c.found.join(", ") || "none"}</td>
                <td className="py-1.5">
                  <ResultBadge result={c.result} />
                </td>
              </tr>
            ))}
          </Table>
        </div>
      )}
    </Card>
  );
}

function Aggregate() {
  const run = useMutation({ mutationFn: () => post("/ai/diagnostics/aggregate") });
  const data = run.data as Json | undefined;
  return (
    <Card title="F. Multi-subject and aggregate testing" description="Bounded runs on the fixture world: scoping before combination, counts instead of names, honest completeness.">
      <Button variant="primary" busy={run.isPending} onClick={() => run.mutate()}>
        Run
      </Button>
      {run.error && <ErrorNote error={run.error} />}
      {data && (
        <div className="mt-4 space-y-4 text-sm">
          <div>
            <h3 className="font-semibold">Several patients, one at a time</h3>
            <p className="text-ink-subtle">{data.multi_subject.explanation}</p>
            <p>
              Requested {data.multi_subject.requested}, included {data.multi_subject.included}, out of scope or missing {data.multi_subject.out_of_scope}, failed{" "}
              {data.multi_subject.failed}.{" "}
              <Badge tone={data.multi_subject.complete ? "ok" : "warn"}>{data.multi_subject.complete ? "complete" : "incomplete"}</Badge>
            </p>
          </div>
          <div>
            <h3 className="font-semibold">Care queue, page of {data.collection.page_size}</h3>
            <ul className="list-disc pl-5">
              {data.collection.items.map((t: string) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="font-semibold">Cycle times (computed in code)</h3>
            <ul className="list-disc pl-5">
              {data.aggregate.metrics.map((t: string) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Card>
  );
}

function Health() {
  const q = useQuery({ queryKey: ["ai-diag", "health"], queryFn: () => api("/ai/diagnostics/health") });
  const probe = useMutation({ mutationFn: () => post("/ai/diagnostics/provider-check") });
  if (q.isLoading) return <LoadingRows />;
  if (q.error) return <ErrorState error={q.error} retry={() => void q.refetch()} />;
  const d = q.data as Json;
  const provider = (probe.data as Json | undefined) ?? d.provider;
  return (
    <div className="space-y-4">
      <Card title="G. Health and operations" description={`Database profile ${d.database_profile} · AI tables ${d.ai_tables ? "present" : "absent"}`}>
        <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
          <span>Provider {provider.provider}:</span>
          <Badge tone={provider.state === "available" ? "ok" : provider.state === "configured_unprobed" ? "neutral" : "warn"}>{label(provider.state)}</Badge>
          {!provider.probed && <span className="text-xs text-ink-subtle">Not probed: configuration only, health unknown.</span>}
          <Button size="sm" busy={probe.isPending} onClick={() => probe.mutate()}>
            Check the local provider
          </Button>
        </div>
        <Table head={["Read service probe", "As role", "State", "ms"]} align={[undefined, undefined, undefined, "right"]}>
          {d.read_service_probes.map((p: Json) => (
            <tr key={p.source} className="border-t border-line">
              <td className="py-1.5 pr-3">{p.source}</td>
              <td className="py-1.5 pr-3 text-xs">{label(p.as_role)}</td>
              <td className="py-1.5 pr-3">
                <Badge tone={p.state === "ok" ? "ok" : p.state === "partial" ? "warn" : "bad"}>{p.state}</Badge>
              </td>
              <td className="py-1.5 text-right">{p.ms}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="AI telemetry (minimised)">
        {d.telemetry.available ? (
          <Table head={["Operation", "Status", "Failure", "Count", "Median ms", "Subjects"]}>
            {d.telemetry.rows.map((r: Json, n: number) => (
              <tr key={n} className="border-t border-line">
                <td className="py-1.5 pr-3">{r.operation}</td>
                <td className="py-1.5 pr-3">{r.status}</td>
                <td className="py-1.5 pr-3">{r.failure ?? ""}</td>
                <td className="py-1.5 pr-3">{r.count}</td>
                <td className="py-1.5 pr-3">{r.median_ms ?? ""}</td>
                <td className="py-1.5">{r.subjects}</td>
              </tr>
            ))}
          </Table>
        ) : (
          <Alert tone="warn">Telemetry unavailable on this database (no AI tables). Not reported as healthy.</Alert>
        )}
      </Card>
      <Card title="Background jobs">
        <Table head={["Kind", "Status", "Count", "Oldest active (min)"]}>
          {d.jobs.map((j: Json) => (
            <tr key={`${j.kind}-${j.status}`} className="border-t border-line">
              <td className="py-1.5 pr-3">{j.kind}</td>
              <td className="py-1.5 pr-3">{j.status}</td>
              <td className="py-1.5 pr-3">{j.count}</td>
              <td className="py-1.5">{j.oldest_minutes ?? ""}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

function Report() {
  const run = useMutation({ mutationFn: () => post("/ai/diagnostics/report") });
  const data = run.data as Json | undefined;
  const download = () => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "ai-context-diagnostics.json";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Card title="H. Diagnostic report" description="Versions, checks, results and failures. The server refuses to produce a report carrying identifiers, emails, names or canary text.">
      <div className="flex gap-2">
        <Button variant="primary" busy={run.isPending} onClick={() => run.mutate()}>
          Generate report
        </Button>
        {data && <Button onClick={download}>Download JSON</Button>}
      </div>
      {run.error && <ErrorNote error={run.error} />}
      {data && (
        <p className="mt-3 text-sm">
          {data.failures.length ? `${data.failures.length} failures: ${data.failures.join(", ")}` : "No failures."} Model calls: {data.model_calls}.
        </p>
      )}
    </Card>
  );
}

export default function AiDiagnostics() {
  const [tab, setTab] = useState<Tab>("sources");
  return (
    <>
      <PageHeader
        title="AI context diagnostics"
        subtitle="Inspect and test the shared AI context layer. Infrastructure only: no model is called and no record changes."
      />
      <div role="tablist" aria-label="Diagnostics sections" className="mb-4 flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button
            key={t.value}
            role="tab"
            type="button"
            aria-selected={tab === t.value}
            onClick={() => setTab(t.value)}
            className={cx(
              "min-h-9 rounded-lg px-3 text-sm font-semibold ring-1 ring-inset",
              tab === t.value ? "bg-ink text-surface ring-ink" : "bg-surface text-ink ring-line-strong hover:bg-subtle",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "sources" && <Sources />}
        {tab === "context" && <ContextPlayground />}
        {tab === "safety" && <Safety />}
        {tab === "aggregate" && <Aggregate />}
        {tab === "health" && <Health />}
        {tab === "report" && <Report />}
      </div>
    </>
  );
}
