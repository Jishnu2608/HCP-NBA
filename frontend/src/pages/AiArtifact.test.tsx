import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../theme";
import { AiArtifactPanel } from "./AiArtifact";
import { CareAiPanels } from "./Care";

const auth = vi.hoisted(() => ({ perms: new Set<string>(["ai:queue_brief", "ai:cycle_time", "ai:decision_trace"]) }));
vi.mock("../auth", () => ({
  useAuth: () => ({ user: { id: 1, role: "care_manager" }, can: (p: string) => auth.perms.has(p) }),
}));

const statement = (text: string, origin: string, citations = ["ctx:queue_counts:1"]) => ({ text, citations, origin });

function view(overrides: Record<string, unknown> = {}) {
  return {
    operation: "queue_brief",
    operation_version: "1",
    mode: "prototype",
    as_of: "2026-10-19",
    source: "template",
    review_required: false,
    can_generate: true,
    result: {
      statements: [
        { label: "Your queue", statement: statement("Requests by status: awaiting hcp 2. In the queue: 2; overdue: 1.", "template") },
        { label: "Start with", statement: statement("Start with queue #1: consultation, awaiting hcp (overdue).", "template", ["ctx:queue_item:1"]) },
      ],
      key_points: [statement("Queue #1: consultation, awaiting hcp, due 2026-10-15 (overdue).", "record", ["ctx:queue_item:1"])],
      notes: ["The order is the server's (most urgent first), and the counts are computed by the server over the whole queue."],
      not_recorded: [],
    },
    sources: [
      { id: "ctx:queue_counts:1", label: "Queue counts", text: "Requests by status…", recorded_at: null, model_visible: true },
      { id: "ctx:queue_item:1", label: "Queue item", text: "Queue #1…", recorded_at: "2026-10-14", model_visible: true },
    ],
    notes: [],
    context: { complete: true, policy_version: "x", categories: [] },
    generation: null,
    ...overrides,
  };
}

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

function renderUi(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

const panel = (subjectId?: string) => (
  <AiArtifactPanel operation="queue_brief" subjectId={subjectId} title="Queue brief" description="Your queue in a sentence." />
);

afterEach(() => {
  vi.unstubAllGlobals();
  auth.perms = new Set(["ai:queue_brief", "ai:cycle_time", "ai:decision_trace"]);
});

describe("AI artifact panel", () => {
  it("shows the template, its sources, the record and the notes about the data", async () => {
    mockServer({ "GET /api/ai/queue_brief/current": { status: 200, body: view() } });
    renderUi(panel());
    expect(await screen.findByText(/In the queue: 2; overdue: 1/)).toBeTruthy();
    expect(screen.getByText("Start with")).toBeTruthy();
    expect(screen.getAllByText("Queue item").length).toBeGreaterThan(0); // source chip
    expect(screen.getByRole("region", { name: "From the record" })).toBeTruthy();
    expect(screen.getByText(/computed by the server over the whole queue/)).toBeTruthy();
    expect(screen.queryByText(/review/i, { selector: "[role=note]" })).toBeNull();
  });

  it("marks model wording for review", async () => {
    const model = view({
      source: "model",
      review_required: true,
      result: {
        ...view().result,
        statements: [{ label: "Your queue", statement: statement("Two requests wait on HCPs; one is overdue.", "model") }],
      },
    });
    mockServer({ "GET /api/ai/queue_brief/current": { status: 200, body: model } });
    const { container } = renderUi(panel());
    expect(await screen.findByText("Two requests wait on HCPs; one is overdue.")).toBeTruthy();
    expect(container.querySelector('[data-origin="model"]')).toBeTruthy();
    expect(screen.getAllByText(/review/i).length).toBeGreaterThan(0);
  });

  it("offers Generate only when the server allows it and sends the subject id", async () => {
    const user = userEvent.setup();
    const get = "/api/ai/decision_trace/current?subject_id=42";
    const calls = mockServer({
      [`GET ${get}`]: { status: 200, body: view({ operation: "decision_trace" }) },
      "POST /api/ai/decision_trace/generations": { status: 202, body: { generation_id: 1, status: "queued", created: true } },
    });
    renderUi(<AiArtifactPanel operation="decision_trace" subjectId="42" title="Decision trace" description="Why." />);
    await user.click(await screen.findByRole("button", { name: /generate/i }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const sent = calls.find((c) => c.method === "POST");
    expect(sent?.path).toBe("/api/ai/decision_trace/generations");
    expect(JSON.parse(sent?.body ?? "{}")).toEqual({ subject_id: "42" });
  });

  it("hides Generate in template mode", async () => {
    mockServer({ "GET /api/ai/queue_brief/current": { status: 200, body: view({ mode: "template", can_generate: false }) } });
    renderUi(panel());
    expect(await screen.findByText(/AI wording is switched off here/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /generate/i })).toBeNull();
  });

  it("shows loading, then an error with a retry, and the no-AI-tables notice", async () => {
    mockServer({ "GET /api/ai/queue_brief/current": { status: 500, body: { detail: "boom" } } });
    const first = renderUi(panel());
    expect(screen.getByText(/Loading queue brief/i)).toBeTruthy();
    expect(await screen.findByText("Queue brief could not be loaded")).toBeTruthy();
    first.unmount();
    vi.unstubAllGlobals();
    mockServer({
      "GET /api/ai/queue_brief/current": { status: 503, body: { detail: { code: "ai_unavailable", message: "x" } } },
    });
    renderUi(panel());
    expect(await screen.findByText("Queue brief not available here")).toBeTruthy();
  });
});

describe("role-based visibility on the care requests page", () => {
  it("shows the queue and cycle-time panels only with their permissions", async () => {
    mockServer({
      "GET /api/ai/queue_brief/current": { status: 200, body: view() },
      "GET /api/ai/cycle_time/current": { status: 200, body: view({ operation: "cycle_time" }) },
    });
    const { unmount } = renderUi(<CareAiPanels />);
    expect(await screen.findByText("Queue brief")).toBeTruthy();
    expect(screen.getByText("Cycle times")).toBeTruthy();
    unmount();
    auth.perms = new Set(["ai:cycle_time"]);
    const second = renderUi(<CareAiPanels />);
    expect(await screen.findByText("Cycle times")).toBeTruthy();
    expect(screen.queryByText("Queue brief")).toBeNull();
    second.unmount();
    auth.perms = new Set();
    const { container } = renderUi(<CareAiPanels />);
    expect(container.textContent).toBe("");
    expect(within(container).queryByRole("region")).toBeNull();
  });
});
