// Care management for care managers: the queue of care requests across their panel, the
// care panel on a real patient's 360 page (confirm what the patient reported, record what
// the clinic established, route to an HCP by specialty, invite a clinic patient), and the
// form that sets up a clinic patient. The server checks the panel and the permission on
// every call; nothing here decides access.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ClipboardList,
  Copy,
  HandHeart,
  HeartPulse,
  Mail,
  MessageSquarePlus,
  Pill,
  Plus,
  RefreshCcw,
  Stethoscope,
  UserPlus,
} from "lucide-react";
import { useCallback, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, patch, post, put, query } from "../api";
import type { Json } from "../api";
import { ResidenceFields } from "../legal";
import { useToast } from "../toast";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Drawer,
  EmptyState,
  ErrorNote,
  ErrorState,
  KpiGrid,
  LoadingRows,
  PageHeader,
  Segmented,
  Stat,
  TextArea,
  TextField,
  fmtDate,
  fmtDateTime,
  num,
} from "../ui";
import type { Column } from "../ui";
import {
  CareTeam,
  ConditionForm,
  ConditionList,
  MedicationForm,
  MedicationSummary,
  NoteList,
  REQUEST_LABEL,
  RequestList,
  StatusChip,
  SupplyFields,
  specialtyText,
  todayISO,
} from "./HealthForms";

const ORIGIN_LABEL: Record<string, string> = {
  synthetic: "Demo record",
  self_registered: "Self-registered",
  clinic: "Clinic patient",
};

export function OriginBadge({ origin }: { origin: string | null | undefined }) {
  if (!origin || origin === "synthetic") return null;
  return <Badge tone="info">{ORIGIN_LABEL[origin] ?? origin}</Badge>;
}

/* ------------------------------------------------------------------ request queue */

type StatusFilter = "open" | "in_progress" | "closed" | "";

export default function CareRequestsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<StatusFilter>("open");
  const [creating, setCreating] = useState(false);
  const close = useCallback(() => setCreating(false), []);
  const list = useQuery({
    queryKey: ["care-requests", status],
    queryFn: () => api(`/care/requests${query({ status })}`),
    placeholderData: (previous) => previous,
  });
  const counts: Record<string, number> = list.data?.counts ?? {};
  const shown = (n: number) => (list.data ? num(n) : "—");
  const columns: Column<Json>[] = [
    {
      key: "patient",
      header: "Patient",
      primary: true,
      className: "min-w-52",
      cell: (r) => (
        <div className="min-w-0">
          <Link
            to={`/patients/${r.patient_id}`}
            onClick={(e) => e.stopPropagation()}
            className="block font-semibold text-ink [overflow-wrap:anywhere] hover:text-primary-ink hover:underline"
          >
            {r.patient_name}
          </Link>
          <div className="tabular text-xs text-ink-subtle">{r.patient_id}</div>
        </div>
      ),
    },
    { key: "type", header: "Request", cell: (r) => <span className="text-ink">{REQUEST_LABEL[r.type] ?? r.type}</span> },
    {
      key: "detail",
      header: "Details",
      hideOnMobile: true,
      cell: (r) => (
        <span className="line-clamp-2 text-ink-muted">{r.condition?.label ?? r.medication ?? r.reason ?? "—"}</span>
      ),
    },
    { key: "created", header: "Received", cell: (r) => <span className="tabular text-ink-muted">{fmtDate(r.created_at)}</span> },
    { key: "status", header: "Status", hideOnMobile: true, cell: (r) => <StatusChip status={r.status} staff /> },
  ];
  return (
    <>
      <PageHeader
        title="Care requests"
        subtitle="What your patients reported or asked for. Confirm what they entered, route them to the right healthcare professional, and record follow-up."
        action={
          <Button variant="primary" onClick={() => setCreating(true)}>
            <UserPlus className="h-4 w-4" aria-hidden /> New clinic patient
          </Button>
        }
      />
      <KpiGrid>
        <Stat label="Open" value={shown(counts.open ?? 0)} tone={counts.open ? "warn" : undefined} icon={<ClipboardList className="h-4 w-4" aria-hidden />} />
        <Stat label="In progress" value={shown(counts.in_progress ?? 0)} icon={<RefreshCcw className="h-4 w-4" aria-hidden />} />
        <Stat label="Closed" value={shown(counts.closed ?? 0)} tone="ok" icon={<Check className="h-4 w-4" aria-hidden />} />
      </KpiGrid>
      <div className="mb-4">
        <Segmented
          label="Filter by status"
          value={status}
          onChange={setStatus}
          options={[
            { value: "open", label: "Open", count: counts.open ?? 0 },
            { value: "in_progress", label: "In progress", count: counts.in_progress ?? 0 },
            { value: "closed", label: "Closed", count: counts.closed ?? 0 },
            { value: "", label: "All" },
          ]}
        />
      </div>
      <Card flush>
        {list.isLoading ? (
          <div className="p-5">
            <LoadingRows rows={6} label="Loading care requests" />
          </div>
        ) : list.error ? (
          <ErrorState error={list.error} retry={() => void list.refetch()} title="Care requests could not be loaded" />
        ) : !list.data.items.length ? (
          <EmptyState title="Nothing waiting" icon={<HandHeart className="h-5 w-5" />}>
            Requests appear here when a patient adds a condition or medication, or asks to consult a healthcare
            professional.
          </EmptyState>
        ) : (
          <DataTable
            caption="Care requests"
            columns={columns}
            rows={list.data.items}
            rowKey={(r) => r.id}
            onRowClick={(r) => navigate(`/patients/${r.patient_id}`)}
            mobileAside={(r) => <StatusChip status={r.status} staff />}
          />
        )}
      </Card>
      {creating && (
        <Drawer title="New clinic patient" onClose={close}>
          <ClinicPatientForm onCreated={(pid) => navigate(`/patients/${pid}`)} />
        </Drawer>
      )}
    </>
  );
}

function ClinicPatientForm({ onCreated }: { onCreated: (patientId: string) => void }) {
  const toast = useToast();
  const [v, setV] = useState({ name: "", date_of_birth: "", country: "", region: null as string | null });
  const [tried, setTried] = useState(false);
  const errors = {
    name: !v.name.trim() ? "Enter the patient's name." : null,
    date_of_birth: !v.date_of_birth ? "Enter the date of birth." : null,
    country: !v.country ? "Choose the country of residence." : null,
    region: v.country === "US" && !v.region ? "Choose the state." : null,
  };
  const create = useMutation({
    mutationFn: () => post("/care/patients", { ...v, name: v.name.trim() }),
    onSuccess: (record: Json) => {
      toast("Clinic patient created. Record their care, then invite them to the portal.");
      onCreated(record.patient_id);
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    if (!Object.values(errors).some(Boolean)) create.mutate();
  }
  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <p className="text-sm text-ink-muted">
        For a person seen at the clinic. You become their responsible care manager. Their record starts empty; record
        what the clinic established, then invite them to the portal.
      </p>
      <TextField label="Full name" value={v.name} maxLength={128} required onChange={(e) => setV({ ...v, name: e.target.value })} error={tried ? errors.name : null} />
      <TextField
        label="Date of birth"
        type="date"
        value={v.date_of_birth}
        required
        onChange={(e) => setV({ ...v, date_of_birth: e.target.value })}
        error={tried ? errors.date_of_birth : null}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <ResidenceFields
          country={v.country}
          region={v.region}
          onChange={(country, region) => setV({ ...v, country, region })}
          errors={tried ? { country: errors.country, region: errors.region } : {}}
        />
      </div>
      <ErrorNote error={create.error} />
      <Button type="submit" variant="primary" busy={create.isPending}>
        Create patient record
      </Button>
    </form>
  );
}

/* ------------------------------------------------------------------ care panel */

type Sheet =
  | { kind: "condition" }
  | { kind: "medication" }
  | { kind: "confirm"; m: Json }
  | { kind: "fill"; m: Json }
  | { kind: "note" }
  | { kind: "hcp"; conditionId?: number; requestId?: number }
  | { kind: "close"; r: Json }
  | null;

const emptySupply = { measure: "", days_supply: "30", copay: "0" };

/** Everything a care manager does for one real patient. */
export function CarePanel({ patientId }: { patientId: string }) {
  const client = useQueryClient();
  const toast = useToast();
  const key = ["care-record", patientId];
  const record = useQuery({ queryKey: key, queryFn: () => api(`/care/patients/${patientId}`) });
  const [sheet, setSheet] = useState<Sheet>(null);
  const close = useCallback(() => setSheet(null), []);
  const done = (message: string) => (data: Json) => {
    client.setQueryData(key, data.record ?? data);
    void client.invalidateQueries({ queryKey: ["patient", patientId] });
    void client.invalidateQueries({ queryKey: ["care-requests"] });
    setSheet(null);
    toast(message);
  };
  const call = useMutation({
    mutationFn: (v: { method: "post" | "put" | "patch"; path: string; body: unknown; message: string }) =>
      (v.method === "put" ? put : v.method === "patch" ? patch : post)(v.path, v.body),
    onSuccess: (data, v) => done(v.message)(data),
  });
  const run = (method: "post" | "put" | "patch", path: string, body: unknown, message: string) =>
    call.mutate({ method, path, body, message });

  if (record.isLoading) return <LoadingRows rows={5} label="Loading care record" />;
  if (record.error) return <ErrorState error={record.error} retry={() => void record.refetch()} title="The care record could not be loaded" />;
  const r: Json = record.data;
  const busy = call.isPending;

  const sheetTitle: Record<string, string> = {
    condition: "Record a condition",
    medication: "Record a medication",
    confirm: "Confirm medication",
    fill: "Log a refill",
    note: "Add an instruction or follow-up",
    hcp: "Route to a healthcare professional",
    close: "Close request",
  };

  return (
    <section aria-labelledby="care-title" className="mb-6 space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="care-title" className="flex items-center gap-2 text-[17px] font-semibold text-ink">
            <HandHeart className="h-5 w-5 text-ink-subtle" aria-hidden /> Care management
          </h2>
          <p className="mt-0.5 text-sm text-ink-subtle">
            Entries the patient made wait here for your review. Only confirmed medications count for adherence.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => setSheet({ kind: "hcp" })}>
            <Stethoscope className="h-3.5 w-3.5" aria-hidden /> Route to HCP
          </Button>
          <Button size="sm" onClick={() => setSheet({ kind: "note" })}>
            <MessageSquarePlus className="h-3.5 w-3.5" aria-hidden /> Add instruction
          </Button>
        </div>
      </div>

      {r.origin === "clinic" && <PortalAccess patientId={patientId} portal={r.portal} onInvited={done("Invitation sent.")} />}

      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Card
          title="Conditions"
          action={
            <Button size="sm" onClick={() => setSheet({ kind: "condition" })}>
              <Plus className="h-3.5 w-3.5" aria-hidden /> Record
            </Button>
          }
        >
          <ConditionList
            items={r.conditions}
            staff
            actions={(c) => (
              <>
                {c.status === "reported" && (
                  <Button size="sm" variant="primary" busy={busy} onClick={() => run("post", `/care/conditions/${c.id}/status`, { status: "confirmed" }, "Condition confirmed.")}>
                    Confirm
                  </Button>
                )}
                {c.status !== "resolved" && (
                  <Button size="sm" onClick={() => setSheet({ kind: "hcp", conditionId: c.id })}>
                    Find HCP
                  </Button>
                )}
                {c.status === "confirmed" && (
                  <Button size="sm" variant="ghost" busy={busy} onClick={() => run("post", `/care/conditions/${c.id}/status`, { status: "resolved" }, "Condition marked resolved.")}>
                    Resolve
                  </Button>
                )}
              </>
            )}
          />
        </Card>
        <Card title="Care team">
          <CareTeam team={r.care_team} />
        </Card>
      </div>

      <Card
        title="Medications"
        description="Reported by the patient or recorded by you. Confirm with the supply details to start adherence tracking."
        action={
          <Button size="sm" onClick={() => setSheet({ kind: "medication" })}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> Record
          </Button>
        }
      >
        {!r.medications.length ? (
          <p className="text-sm text-ink-subtle">No medications recorded.</p>
        ) : (
          <ul className="divide-y divide-line">
            {r.medications.map((m: Json) => (
              <li key={m.therapy_id} className="flex flex-col gap-3 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
                <MedicationSummary m={m} staff />
                <div className="flex shrink-0 flex-wrap gap-2">
                  {m.review_status === "reported" && (
                    <Button size="sm" variant="primary" onClick={() => setSheet({ kind: "confirm", m })}>
                      Confirm
                    </Button>
                  )}
                  {m.review_status === "confirmed" && (
                    <Button size="sm" onClick={() => setSheet({ kind: "fill", m })}>
                      <Pill className="h-3.5 w-3.5" aria-hidden /> Log refill
                    </Button>
                  )}
                  {m.review_status !== "stopped" && (
                    <Button size="sm" variant="ghost" busy={busy} onClick={() => run("post", `/care/medications/${m.therapy_id}/stop`, {}, "Medication stopped.")}>
                      Stop
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Card title="Requests">
          <RequestList
            items={r.requests}
            staff
            actions={(q) =>
              q.status !== "closed" && (
                <>
                  {q.type === "consultation" && (
                    <Button size="sm" variant="primary" onClick={() => setSheet({ kind: "hcp", requestId: q.id })}>
                      Route to HCP
                    </Button>
                  )}
                  {q.status === "open" && (
                    <Button size="sm" busy={busy} onClick={() => run("patch", `/care/requests/${q.id}`, { status: "in_progress" }, "Marked in progress.")}>
                      Start
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => setSheet({ kind: "close", r: q })}>
                    Close
                  </Button>
                </>
              )
            }
          />
        </Card>
        <Card title="Instructions and follow-up">
          <NoteList items={r.notes} staff />
        </Card>
      </div>
      <ErrorNote error={call.error} />

      {sheet && (
        <Drawer title={sheetTitle[sheet.kind]} onClose={close}>
          {sheet.kind === "condition" && (
            <ConditionForm
              submitLabel="Record condition"
              busy={busy}
              error={call.error}
              onSubmit={(b) => run("post", `/care/patients/${patientId}/conditions`, b, "Condition recorded.")}
            />
          )}
          {sheet.kind === "medication" && <RecordMedication patientId={patientId} run={run} busy={busy} error={call.error} />}
          {sheet.kind === "confirm" && <ConfirmMedication m={sheet.m} run={run} busy={busy} error={call.error} />}
          {sheet.kind === "fill" && <LogFill m={sheet.m} today={todayISO()} run={run} busy={busy} error={call.error} />}
          {sheet.kind === "note" && <NoteForm patientId={patientId} hcps={r.care_team.hcps} run={run} busy={busy} error={call.error} />}
          {sheet.kind === "close" && <CloseRequest r={sheet.r} run={run} busy={busy} error={call.error} />}
          {sheet.kind === "hcp" && (
            <HcpPicker
              patientId={patientId}
              conditions={r.conditions.filter((c: Json) => c.status !== "resolved")}
              conditionId={sheet.conditionId}
              requestId={sheet.requestId}
              run={run}
              busy={busy}
              error={call.error}
            />
          )}
        </Drawer>
      )}
    </section>
  );
}

type Run = (method: "post" | "put" | "patch", path: string, body: unknown, message: string) => void;

function supplyBody(s: typeof emptySupply) {
  return { measure: s.measure || null, days_supply: Number(s.days_supply), copay: Number(s.copay || 0) };
}

function RecordMedication({ patientId, run, busy, error }: { patientId: string; run: Run; busy: boolean; error: unknown }) {
  const [supply, setSupply] = useState(emptySupply);
  return (
    <MedicationForm
      audience="staff"
      submitLabel="Record medication"
      busy={busy}
      error={error}
      extra={<SupplyFields value={supply} onChange={setSupply} />}
      onSubmit={(b) => run("post", `/care/patients/${patientId}/medications`, { ...b, ...supplyBody(supply) }, "Medication recorded.")}
    />
  );
}

function ConfirmMedication({ m, run, busy, error }: { m: Json; run: Run; busy: boolean; error: unknown }) {
  const [supply, setSupply] = useState(emptySupply);
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-line p-4">
        <MedicationSummary m={m} staff />
      </div>
      <SupplyFields value={supply} onChange={setSupply} knownMeasure={m.measure} />
      <ErrorNote error={error} />
      <Button
        variant="primary"
        busy={busy}
        onClick={() => run("post", `/care/medications/${m.therapy_id}/confirm`, supplyBody(supply), "Medication confirmed.")}
      >
        <Check className="h-4 w-4" aria-hidden /> Confirm medication
      </Button>
    </div>
  );
}

function LogFill({ m, today, run, busy, error }: { m: Json; today: string; run: Run; busy: boolean; error: unknown }) {
  const [day, setDay] = useState(today);
  return (
    <div className="space-y-4">
      <MedicationSummary m={m} staff />
      <TextField label="Fill date" type="date" max={today} min={m.start_date} value={day} onChange={(e) => setDay(e.target.value)} />
      <ErrorNote error={error} />
      <Button variant="primary" busy={busy} onClick={() => run("post", `/care/medications/${m.therapy_id}/fills`, { fill_date: day }, "Refill logged.")}>
        Log refill
      </Button>
    </div>
  );
}

function NoteForm({ patientId, hcps, run, busy, error }: { patientId: string; hcps: Json[]; run: Run; busy: boolean; error: unknown }) {
  const [kind, setKind] = useState<"hcp_instruction" | "follow_up">("hcp_instruction");
  const [text, setText] = useState("");
  const [hcp, setHcp] = useState("");
  const [visible, setVisible] = useState(true);
  const [tried, setTried] = useState(false);
  return (
    <form
      className="space-y-4"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        setTried(true);
        if (text.trim())
          run("post", `/care/patients/${patientId}/notes`, { kind, text: text.trim(), hcp_id: hcp || null, visible_to_patient: visible }, "Saved.");
      }}
    >
      <Segmented
        label="Kind"
        value={kind}
        onChange={setKind}
        options={[
          { value: "hcp_instruction", label: "HCP instruction" },
          { value: "follow_up", label: "Follow-up" },
        ]}
      />
      {hcps.length > 0 && (
        <label className="block text-sm font-medium text-ink">
          From
          <select
            value={hcp}
            onChange={(e) => setHcp(e.target.value)}
            className="mt-1.5 block h-11 w-full rounded-lg border border-line-strong bg-surface px-3 text-[15px] text-ink shadow-card"
          >
            <option value="">Not from a specific HCP</option>
            {hcps.map((h) => (
              <option key={h.hcp_id} value={h.hcp_id}>
                {h.name} · {specialtyText(h.specialties)}
              </option>
            ))}
          </select>
        </label>
      )}
      <TextArea
        label="Text"
        rows={5}
        maxLength={1000}
        value={text}
        required
        onChange={(e) => setText(e.target.value)}
        error={tried && !text.trim() ? "Write the instruction or follow-up." : null}
      />
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={visible} onChange={(e) => setVisible(e.target.checked)} className="h-4 w-4" />
        Show to the patient
      </label>
      <ErrorNote error={error} />
      <Button type="submit" variant="primary" busy={busy}>
        Save
      </Button>
    </form>
  );
}

function CloseRequest({ r, run, busy, error }: { r: Json; run: Run; busy: boolean; error: unknown }) {
  const [resolution, setResolution] = useState("");
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-muted">{REQUEST_LABEL[r.type]} · received {fmtDate(r.created_at)}</p>
      <TextArea label="What was done" rows={4} maxLength={500} value={resolution} onChange={(e) => setResolution(e.target.value)} />
      <ErrorNote error={error} />
      <Button
        variant="primary"
        busy={busy}
        onClick={() => run("patch", `/care/requests/${r.id}`, { status: "closed", resolution: resolution.trim() || null }, "Request closed.")}
      >
        Close request
      </Button>
    </div>
  );
}

function HcpPicker({
  patientId,
  conditions,
  conditionId,
  requestId,
  run,
  busy,
  error,
}: {
  patientId: string;
  conditions: Json[];
  conditionId?: number;
  requestId?: number;
  run: Run;
  busy: boolean;
  error: unknown;
}) {
  const [condition, setCondition] = useState<string>(conditionId ? String(conditionId) : conditions[0] ? String(conditions[0].id) : "");
  const options = useQuery({
    queryKey: ["hcp-options", patientId, condition],
    queryFn: () => api(`/care/patients/${patientId}/hcp-options${query({ condition_id: condition })}`),
  });
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-muted">
        Healthcare professionals whose specialty suits the condition: the specialist first, then primary care. You
        choose; nobody is assigned automatically.
      </p>
      {conditions.length > 0 && (
        <label className="block text-sm font-medium text-ink">
          For condition
          <select
            value={condition}
            onChange={(e) => setCondition(e.target.value)}
            className="mt-1.5 block h-11 w-full rounded-lg border border-line-strong bg-surface px-3 text-[15px] text-ink shadow-card"
          >
            {conditions.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
            {requestId && <option value="">No specific condition (primary care)</option>}
          </select>
        </label>
      )}
      {!conditions.length && <Alert tone="info" title="No condition recorded">Primary care is offered. Record a condition to see specialists.</Alert>}
      <ErrorNote error={error} />
      {options.isLoading ? (
        <LoadingRows rows={4} label="Loading HCPs" />
      ) : options.error ? (
        <ErrorNote error={options.error} />
      ) : (
        <ul className="divide-y divide-line rounded-xl border border-line">
          {(options.data?.items ?? []).map((h: Json) => (
            <li key={h.hcp_id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-ink">{h.name}</div>
                <div className="text-[13px] text-ink-subtle">
                  {h.reason}
                  {h.location ? ` · ${h.location}` : ""}
                  {h.origin === "invited" ? " · Invited HCP" : ""}
                </div>
                <div className="mt-1 text-[13px] text-ink-muted">All specialties: {specialtyText(h.specialties)}</div>
              </div>
              <Button
                size="sm"
                busy={busy}
                onClick={() =>
                  run(
                    "put",
                    `/care/patients/${patientId}/hcp`,
                    { hcp_id: h.hcp_id, condition_id: condition ? Number(condition) : null, request_id: requestId ?? null },
                    `Routed to ${h.name}.`,
                  )
                }
              >
                Assign
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const PORTAL_STATE: Record<string, { tone: "ok" | "warn" | "info" | "neutral"; label: string }> = {
  none: { tone: "neutral", label: "Not invited" },
  invited: { tone: "info", label: "Invitation sent" },
  pending: { tone: "warn", label: "Verifying email" },
  active: { tone: "ok", label: "Active" },
  expired: { tone: "warn", label: "Invitation expired" },
  revoked: { tone: "neutral", label: "Invitation withdrawn" },
};

function PortalAccess({ patientId, portal, onInvited }: { patientId: string; portal: Json; onInvited: (data: Json) => void }) {
  const [email, setEmail] = useState(portal.email ?? "");
  const [link, setLink] = useState<string | null>(null);
  const invite = useMutation({
    mutationFn: () => post(`/care/patients/${patientId}/invite`, { email: email.trim() }),
    onSuccess: (data: Json) => {
      setLink(data.dev_link ?? null);
      onInvited(data);
    },
  });
  const state: string = portal.state;
  let body: ReactNode;
  if (state === "active" || state === "pending") {
    body = (
      <p className="text-sm text-ink-muted">
        {state === "active" ? "Portal account active" : "Account created, waiting for email verification"} ·{" "}
        <span className="break-all font-semibold text-ink">{portal.email}</span>
      </p>
    );
  } else {
    body = (
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (email.trim()) invite.mutate();
        }}
      >
        {/* Input and button share one row and one height; the hint sits below both, so it
            never pushes the button out of line. Stacks on phones. */}
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <TextField
            label="Patient's email"
            type="email"
            inputMode="email"
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="min-w-0 flex-1"
          />
          <Button type="submit" variant="primary" busy={invite.isPending} className="h-11 w-full shrink-0 sm:w-auto">
            <Mail className="h-4 w-4" aria-hidden /> {state === "invited" ? "Send again" : "Invite to portal"}
          </Button>
        </div>
        <p className="mt-1.5 text-[13px] text-ink-subtle">
          {state === "invited"
            ? `Invitation sent, expires ${fmtDateTime(`${portal.expires_at}Z`)}. Sending again replaces it.`
            : "The invitation is bound to this record. The patient sees what you recorded once they join."}
        </p>
      </form>
    );
  }
  return (
    <Card title="Patient portal" action={<Badge tone={PORTAL_STATE[state]?.tone ?? "neutral"}>{PORTAL_STATE[state]?.label ?? state}</Badge>}>
      {body}
      <ErrorNote error={invite.error} className="mt-3" />
      {link && (
        <Alert tone="info" title="No email was sent (local run without a mail server)" className="mt-3">
          <div className="flex flex-wrap items-center gap-2">
            <code className="break-all text-[13px]">{link}</code>
            <Button size="sm" onClick={() => void navigator.clipboard?.writeText(link)}>
              <Copy className="h-3.5 w-3.5" aria-hidden /> Copy
            </Button>
          </div>
        </Alert>
      )}
    </Card>
  );
}

/** Read-only summary for staff who see the patient but do not manage their care. */
export function HealthSummary({ health }: { health: Json }) {
  return (
    <section aria-labelledby="health-title" className="mb-6 grid items-start gap-6 lg:grid-cols-3">
      <h2 id="health-title" className="sr-only">
        Health profile
      </h2>
      <Card title={<span className="flex items-center gap-2"><HeartPulse className="h-4 w-4 text-ink-subtle" aria-hidden /> Conditions</span>}>
        <ConditionList items={health.conditions} staff />
      </Card>
      <Card title="Medications">
        {health.medications.length ? (
          <ul className="space-y-3">
            {health.medications.map((m: Json) => (
              <li key={m.therapy_id}>
                <MedicationSummary m={m} staff />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-subtle">No medications recorded.</p>
        )}
      </Card>
      <Card title="Requests">
        <RequestList items={health.requests} staff />
      </Card>
    </section>
  );
}
