import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { P } from "../permissions";
import { mayOpen } from "../routes";
import { ThemeProvider } from "../theme";
import AiDiagnostics from "./AiDiagnostics";

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
          <AiDiagnostics />
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

const SOURCES = {
  policy_version: "2026-10-17.1",
  contract_version: "2026-10-17.1",
  sources: [
    {
      id: "patient_360",
      domain: "Patient 360 (staff)",
      status: "implemented",
      status_reason: null,
      availability: "ok",
      subject_types: [{ type: "patient", scope: "single" }],
      categories: [
        { name: "rx", record: "therapy", model: true, description: "Refills" },
        { name: "consent", record: "patient_360", model: false, description: "Consent" },
      ],
      read_services: ["services.patients.patient_profile"],
      principal_dependent: false,
    },
    {
      id: "inbox",
      domain: "Patient and HCP inboxes",
      status: "unsupported",
      status_reason: "delivered_wording_personalised_and_free_text_questions",
      availability: "not_applicable",
      subject_types: [],
      categories: [],
      read_services: [],
      principal_dependent: false,
    },
  ],
  operations: [{ id: "case_brief", version: "2", subject_type: "patient", categories: ["rx"], permission: "ai:case_brief" }],
};
const POLICY = { policy_version: "x", records: { therapy: { drug_name: "model_if_vocabulary", copay: "deterministic_only" } }, globally_prohibited: [], model_visible: [] };

afterEach(() => vi.unstubAllGlobals());

describe("AI context diagnostics", () => {
  it("is reachable only with the ai:diagnostics permission", () => {
    expect(mayOpen("/ai-diagnostics", [P.AI_DIAGNOSTICS])).toBe(true);
    expect(mayOpen("/ai-diagnostics", [P.MODELS_READ, P.AUDIT_READ, P.AI_CASE_BRIEF])).toBe(false);
  });

  it("lists sources with their status, including unsupported domains and server-only categories", async () => {
    mockServer({
      "GET /api/ai/diagnostics/sources": { status: 200, body: SOURCES },
      "GET /api/ai/diagnostics/policy": { status: 200, body: POLICY },
    });
    renderPage();
    expect(await screen.findByText("patient_360")).toBeTruthy();
    expect(screen.getByText("unsupported")).toBeTruthy();
    expect(screen.getByText(/consent \(server text only\)/)).toBeTruthy();
    expect(await screen.findByText("model if vocabulary")).toBeTruthy();
  });

  it("marks a record preview as redacted and different from the model payload", async () => {
    const calls = mockServer({
      "GET /api/ai/diagnostics/sources": { status: 200, body: SOURCES },
      "POST /api/ai/diagnostics/context": {
        status: 200,
        body: {
          mode: "record_preview",
          principal: "you (administrator, your own access)",
          checks: [{ check: "permission", result: "pass", code: null }],
          context: {
            source: "patient_360",
            as_of: "2026-10-17",
            complete: true,
            policy_version: "x",
            fingerprint: "a".repeat(64),
            categories: [{ category: "rx", state: "included", count: 1, total: 1, reason: null }],
            items: [{ id: "ctx:rx:1", category: "rx", source: "patient_360.therapies", recorded_at: null, model_visible: true, text: "[redacted]" }],
            model_item_count: 1,
            reader_note_count: 2,
            withheld_count: 3,
            identifier_count: 4,
            field_decisions: {},
            redacted: true,
            differs_from_model_payload: true,
          },
        },
      },
    });
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Context playground" }));
    await userEvent.selectOptions(screen.getByLabelText("Mode"), "record_preview");
    await userEvent.type(screen.getByLabelText("Record id"), "PAT_00001");
    await userEvent.click(screen.getByRole("button", { name: "Build context" }));
    expect(await screen.findByText("Redacted preview: differs from the model payload")).toBeTruthy();
    expect(screen.getByText("[redacted]")).toBeTruthy();
    const sent = calls.find((c) => c.path === "/api/ai/diagnostics/context");
    expect(JSON.parse(sent?.body ?? "{}")).toEqual({ mode: "record_preview", subject_type: "patient", subject: "PAT_00001" });
  });

  it("shows denials as check codes without any record content", async () => {
    mockServer({
      "GET /api/ai/diagnostics/sources": { status: 200, body: SOURCES },
      "POST /api/ai/diagnostics/context": {
        status: 200,
        body: { mode: "fixture", principal: "synthetic medical rep (fixture world)", checks: [{ check: "permission", result: "fail", code: "forbidden" }], context: null },
      },
    });
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Context playground" }));
    await userEvent.click(screen.getByRole("button", { name: "Build context" }));
    expect(await screen.findByText("fail")).toBeTruthy();
    expect(screen.getByText("forbidden")).toBeTruthy();
    expect(screen.queryByText(/D\. Context preview/)).toBeNull();
  });

  it("runs the safety cases and reports pass and fail per rule", async () => {
    mockServer({
      "GET /api/ai/diagnostics/sources": { status: 200, body: SOURCES },
      "POST /api/ai/diagnostics/safety": {
        status: 200,
        body: {
          passed: 1,
          failed: 1,
          note: "Deterministic checks only. No model was called.",
          cases: [
            { id: "absence_when_excluded_by_allowlist", description: "d1", expected: "unsupported_absence:excluded_allowlist", found: ["unsupported_absence:excluded_allowlist"], result: "pass" },
            { id: "fabricated_date", description: "d2", expected: "fabricated_date", found: [], result: "fail" },
          ],
        },
      },
    });
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Safety checks" }));
    await userEvent.click(screen.getByRole("button", { name: "Run checks" }));
    expect(await screen.findByText(/1 passed, 1 failed/)).toBeTruthy();
    expect(screen.getByText("absence when excluded by allowlist")).toBeTruthy();
  });

  it("labels missing telemetry as unavailable, never healthy", async () => {
    mockServer({
      "GET /api/ai/diagnostics/sources": { status: 200, body: SOURCES },
      "GET /api/ai/diagnostics/health": {
        status: 200,
        body: {
          database_profile: "traditional",
          ai_tables: false,
          provider: { provider: "ollama", state: "configured_unprobed", probed: false },
          read_service_probes: [{ source: "patient_360", subject_type: "patient", as_role: "care_manager", state: "ok", error: null, ms: 12 }],
          jobs: [],
          telemetry: { available: false, state: "unavailable" },
          operations: [],
          versions: {},
          limits: {},
        },
      },
    });
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Health" }));
    expect(await screen.findByText(/Not reported as healthy/)).toBeTruthy();
    expect(screen.getByText(/health unknown/)).toBeTruthy();
  });

  it("generates the sanitized report and offers it for download", async () => {
    mockServer({
      "GET /api/ai/diagnostics/sources": { status: 200, body: SOURCES },
      "POST /api/ai/diagnostics/report": { status: 200, body: { failures: [], model_calls: 0 } },
    });
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Report" }));
    await userEvent.click(screen.getByRole("button", { name: "Generate report" }));
    await waitFor(() => expect(screen.getByText(/No failures\. Model calls: 0\./)).toBeTruthy());
    expect(screen.getByRole("button", { name: "Download JSON" })).toBeTruthy();
  });
});
