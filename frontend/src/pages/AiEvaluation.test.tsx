import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../theme";
import AiEvaluation from "./AiEvaluation";

vi.mock("../auth", () => ({
  useAuth: () => ({ user: { id: 1, role: "admin" }, can: () => true }),
}));

type Reply = { status: number; body: unknown };

function mockServer(replies: Record<string, Reply>) {
  const calls: Array<{ path: string; method: string; body?: string }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: typeof init?.body === "string" ? init.body : undefined });
      const reply = replies[`${method} ${path}`] ?? { status: 200, body: {} };
      return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "content-type": "application/json" } });
    }),
  );
  return calls;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AiEvaluation />
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

const TRACE = {
  contract: {
    operation: "probe_summary",
    test_only: true,
    version: "1",
    prompt_version: "probe-1",
    subject_type: "patient",
    permission: "ai:case_brief",
    categories: ["patient", "rx"],
    allow_partial: false,
    max_age_days: null,
    max_context_items: 40,
    max_output_chars: 2000,
  },
  principal: { role: "care_manager", account: "synthetic fixture account" },
  steps: [
    { step: "authorisation", outcome: "allowed" },
    { step: "mode", outcome: "shadow" },
    { step: "disposition", outcome: "fallback", failure: "gate_rejected" },
  ],
  source: {
    id: "patient_360",
    read_services: ["services.patients.patient_profile"],
    principal_dependent: false,
    policy_version: "2026-10-17.1",
    fingerprint: "abcdef0123456789",
    complete: true,
    evidence: [{ id: "ctx:patient:1", category: "patient", source: "patients.profile", source_version: null, dated: "2026-10-17" }],
  },
  policy: [
    { category: "patient", model_eligible: true, in_allowlist: true, state: "included", reason: null },
    { category: "consent", model_eligible: false, in_allowlist: false, state: "excluded_policy", reason: "server_text_only" },
  ],
  prompt: {
    template_version: "probe-1",
    system: "Test-only consumer.",
    sent: true,
    payload_sent: [{ role: "user", content: "[ctx:patient:1] Patient: age 54." }],
    payload_built_not_sent: [],
    payload_redacted: [{ role: "user", content: "[ctx:patient:1] <patient item ctx:patient:1>" }],
    canaries_in_payload: 0,
    identifiers_in_payload: 0,
  },
  output_contract: { paths: { summary: "text", "summary_citations[]": "citation" }, error: null },
  checks: { shared: ["leak:contact", "unsupported_absence"], reasons: ["leak:contact"], findings: [{ kind: "leak:contact", where: "summary" }] },
  provider: { name: "scripted", model: "scripted-test-double", local: true, calls: 1, note: "scripted test double: no model is called by the evaluation" },
  disposition: { status: "fallback", failure: "gate_rejected", output_stored: false },
};

const RESULT = {
  run: {
    at: "2026-10-17T20:00:00Z",
    revision: "6c70f10469b2",
    rubric_version: "rubric-1 (2026-10-17)",
    duration_ms: 6500,
    results_digest: "x",
    model_calls: 0,
    worlds: "two synthetic fixture worlds",
  },
  counts: { scenarios: 3, executed: 3, passed: 3, failed: 0, not_applicable: 0, skipped: 0, unverified_controls: 1 },
  pass_rate: 100,
  score: {
    rubric_version: "rubric-1",
    threshold: 95,
    confidence: 93.1,
    readiness: "below_threshold",
    dimensions: [
      { id: "provider", name: "Provider and network-boundary assurance", weight: 7, score: 80, points: 5.6, controls: 5, evidenced: 4, why: "Local only." },
      { id: "completeness", name: "Completeness, freshness, provenance, reproducibility", weight: 10, score: 100, points: 10, controls: 6, evidenced: 6, why: "Partial data." },
      { id: "output_safety", name: "Evidence and output-safety enforcement", weight: 15, score: 87.5, points: 13.12, controls: 8, evidenced: 7, why: "Unchecked text." },
    ],
    controls: [
      { id: "provider.os_egress", dimension: "provider", name: "Operating-system egress block", kind: "external", status: "unverified", credit: 0, note: "Host rule needed.", scenarios: 0 },
    ],
    hard_blockers: [],
    open_high_findings: [],
    insufficient_dimensions: [],
    unverified_controls: ["provider.os_egress"],
  },
  scenarios: [
    {
      id: "SAF-ADV-PHONE",
      name: "Adversarial answer: leak:contact",
      control: "safety.adversarial_text",
      dimension: "output_safety",
      domain: "patient",
      role: "care manager",
      expected: "fallback: gate_rejected (leak:contact)",
      actual: "fallback: ['leak:contact']",
      status: "pass",
      gate: "safety.check_text_fields",
      refs: ["tests/test_context_field_leakage.py"],
      finding: null,
      hard_blocker: "unchecked_output",
      trace: true,
      ms: 12,
    },
    {
      id: "SAF-P4-01",
      name: "Registration of an output with unchecked text (probe P4)",
      control: "safety.uncovered_text",
      dimension: "output_safety",
      domain: "registry",
      role: "",
      expected: "refused",
      actual: "refused: uncovered output text",
      status: "pass",
      gate: "registry.validate",
      refs: ["tests/test_context_remediation.py::test_p4_uncovered_text_is_refused"],
      finding: "F-01",
      hard_blocker: "unchecked_output",
      trace: false,
      ms: 1,
    },
    {
      id: "CMP-TRUNC-02",
      name: "Collection truncation",
      control: "complete.truncation",
      dimension: "completeness",
      domain: "care_queue",
      role: "care manager",
      expected: "truncated",
      actual: "queue_item truncated",
      status: "pass",
      gate: "collection paging",
      refs: [],
      finding: null,
      hard_blocker: null,
      trace: false,
      ms: 3,
    },
  ],
  traces: { "SAF-ADV-PHONE": TRACE },
  findings: [
    { id: "F-01", severity: "High", title: "Output checks bound to cited_fields", recorded: "verified_closed", status: "verified_closed", evidence: "tests", scenarios: ["SAF-P4-01"], scenarios_passed: 1 },
    { id: "F-05", severity: "Medium", title: "No Ollama outbound firewall rule", recorded: "external_verification_required", status: "external_verification_required", evidence: "script exit 1", scenarios: [], scenarios_passed: 0 },
  ],
  external: [{ control: "provider.os_egress", name: "Operating-system egress block", note: "Host rule needed." }],
  transparency: {
    operations: [{ id: "case_brief", version: "2", prompt_version: "10", subject_type: "patient", categories: ["rx"], cited_fields: [["summary", "summary_citations"]], text_fields: [], system_prompt: "You write two parts." }],
    probe_prompt: { version: "probe-1", system: "Test-only consumer." },
    config: { provider: "off", operation_modes: {}, operations_disabled: [], max_active_jobs: 2, policy_version: "2026-10-17.1", rubric_version: "rubric-1" },
  },
  comparison: {
    previous: { at: "2026-10-17T19:00:00Z", confidence: 78.8, readiness: "blocked", revision: "6c70f10469b2", rubric: "rubric-1", counts: {} },
    changes: [{ id: "SAF-P4-01", from: "fail", to: "pass", kind: "improvement", finding: "F-01", refs: ["tests/test_context_remediation.py::test_p4_uncovered_text_is_refused"] }],
    new_scenarios: [],
  },
  registered_operations: ["case_brief"],
};

afterEach(() => vi.unstubAllGlobals());

describe("AI context evaluation", () => {
  it("says when nothing has been evaluated", async () => {
    mockServer({ "GET /api/ai/diagnostics/evaluation": { status: 200, body: { last: null, readiness: "not_evaluated" } } });
    renderPage();
    expect(await screen.findByText("Not evaluated")).toBeTruthy();
    expect(screen.getByText(/No evaluation recorded yet/)).toBeTruthy();
  });

  it("shows the last recorded run without running anything", async () => {
    const calls = mockServer({
      "GET /api/ai/diagnostics/evaluation": {
        status: 200,
        body: { last: { at: "2026-10-17T20:00:00Z", confidence: 93.1, readiness: "below_threshold", counts: { passed: 114, executed: 114 } }, readiness: "below_threshold" },
      },
    });
    renderPage();
    expect(await screen.findByText(/confidence 93.1/)).toBeTruthy();
    expect(screen.getByText("Below threshold")).toBeTruthy();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("runs with no input and reports confidence separately from the pass rate", async () => {
    const user = userEvent.setup();
    const calls = mockServer({
      "GET /api/ai/diagnostics/evaluation": { status: 200, body: { last: null, readiness: "not_evaluated" } },
      "POST /api/ai/diagnostics/evaluation/run": { status: 200, body: { result: RESULT, export: { ...RESULT, traces: {} } } },
    });
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Run evaluation" }));
    expect(await screen.findByLabelText("Confidence 93.1 of 100")).toBeTruthy();
    expect(screen.getByText(/Not a probability and not the pass rate/)).toBeTruthy();
    expect(screen.getByText("Pass rate 100%")).toBeTruthy();
    expect(screen.getAllByText("Below threshold").length).toBeGreaterThan(0);
    // Unverified external controls are listed, never shown as passed.
    expect(screen.getByText("Controls without runtime evidence (earn no credit)")).toBeTruthy();
    expect(screen.getAllByText("Operating-system egress block").length).toBeGreaterThan(0);
    const run = calls.find((c) => c.path === "/api/ai/diagnostics/evaluation/run");
    expect(run?.method).toBe("POST");
    // No input of any kind: an empty body (no SQL, source, payload or policy can be sent).
    expect(["", "{}"]).toContain(run?.body ?? "");
  });

  it("shows a hard blocker as blocked whatever the score", async () => {
    const user = userEvent.setup();
    const blocked = { ...RESULT, score: { ...RESULT.score, confidence: 97, readiness: "blocked", hard_blockers: [{ code: "data_leakage", label: "Sensitive-data leakage" }] } };
    mockServer({
      "GET /api/ai/diagnostics/evaluation": { status: 200, body: { last: null, readiness: "not_evaluated" } },
      "POST /api/ai/diagnostics/evaluation/run": { status: 200, body: { result: blocked, export: blocked } },
    });
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Run evaluation" }));
    expect(await screen.findByText(/Hard blockers: Sensitive-data leakage/)).toBeTruthy();
    expect(screen.getAllByText("Blocked").length).toBeGreaterThan(0);
    expect(screen.queryByText("Eligible for owner review")).toBeNull();
  });

  it("filters scenarios and opens a trace with redacted and sent payloads", async () => {
    const user = userEvent.setup();
    mockServer({
      "GET /api/ai/diagnostics/evaluation": { status: 200, body: { last: null, readiness: "not_evaluated" } },
      "POST /api/ai/diagnostics/evaluation/run": { status: 200, body: { result: RESULT, export: RESULT } },
    });
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Run evaluation" }));
    expect(await screen.findByText("Showing 3 of 3 scenarios.")).toBeTruthy();
    await user.selectOptions(screen.getByLabelText("Dimension"), "completeness");
    expect(screen.getByText("Showing 1 of 3 scenarios.")).toBeTruthy();
    await user.selectOptions(screen.getByLabelText("Dimension"), "all");
    await user.click(screen.getByRole("button", { name: "Show the inference trace of SAF-ADV-PHONE" }));
    const trace = await screen.findByRole("region", { name: "Prompt template and payload" });
    expect(within(trace).getByText(/<patient item ctx:patient:1>/)).toBeTruthy();
    expect(within(trace).queryByText(/Patient: age 54/)).toBeNull();
    await user.click(within(trace).getByRole("radio", { name: "Actual payload sent" }));
    expect(within(trace).getByText(/Patient: age 54/)).toBeTruthy();
    expect(screen.getByRole("region", { name: "Safety checks and outcomes" }).textContent).toContain("leak:contact at summary");
    expect(screen.getByRole("region", { name: "Final disposition" }).textContent).toContain("gate rejected");
    expect(screen.getByText(/no model is called by the evaluation/)).toBeTruthy();
  });

  it("shows the comparison, the findings register and the prompts in force", async () => {
    const user = userEvent.setup();
    mockServer({
      "GET /api/ai/diagnostics/evaluation": { status: 200, body: { last: null, readiness: "not_evaluated" } },
      "POST /api/ai/diagnostics/evaluation/run": { status: 200, body: { result: RESULT, export: RESULT } },
    });
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Run evaluation" }));
    expect(await screen.findByText("fail → pass")).toBeTruthy();
    expect(screen.getByText(/no other cause of a change is claimed/)).toBeTruthy();
    expect(screen.getByText("Output checks bound to cited_fields")).toBeTruthy();
    expect(screen.getAllByText("external verification required").length).toBeGreaterThan(0);
    expect(screen.getByText("You write two parts.")).toBeTruthy();
  });

  it("reports a refused run (rate limit) without inventing a result", async () => {
    const user = userEvent.setup();
    mockServer({
      "GET /api/ai/diagnostics/evaluation": { status: 200, body: { last: null, readiness: "not_evaluated" } },
      "POST /api/ai/diagnostics/evaluation/run": { status: 429, body: { detail: { code: "rate_limited", message: "Too many attempts. Please try again later." } } },
    });
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Run evaluation" }));
    await waitFor(() => expect(screen.getByText(/Too many attempts/)).toBeTruthy());
    expect(screen.queryByLabelText(/Confidence/)).toBeNull();
  });
});
