// Forms and lists shared by the patient's "My health" page and the care manager's views:
// conditions, medications, care requests, instructions and the care team. What the server
// accepts is checked there; these only collect and display.
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Clock3, HeartPulse, MessageSquareText, Pill, Stethoscope, UserRound } from "lucide-react";
import { useId, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { api } from "../api";
import type { Json } from "../api";
import { Avatar, Badge, Button, ErrorNote, FormField, TextArea, TextField, cx, fmtDate, fmtDateTime, titleCase } from "../ui";
import type { Tone } from "../ui";

export interface Vocabulary {
  conditions: Array<{ code: string; label: string; specialties: string[] }>;
  medications: Array<{ name: string; measure: string }>;
  measures: string[];
  days_supply: number[];
}

/** Today as YYYY-MM-DD. The server works on the UTC date, so forms do too (it checks
 *  again). */
export const todayISO = () => new Date().toISOString().slice(0, 10);

const addDays = (iso: string, days: number) => {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return d.toLocaleDateString("en-CA");
};

export const useVocabulary = () =>
  useQuery({ queryKey: ["care-vocabulary"], queryFn: () => api<Vocabulary>("/care/vocabulary"), staleTime: Infinity });

const MEASURE_LABEL: Record<string, string> = {
  diabetes: "Diabetes",
  hypertension: "Blood pressure",
  cholesterol: "Cholesterol",
};
export const measureLabel = (m: string | null | undefined) => (m ? (MEASURE_LABEL[m] ?? titleCase(m)) : "Not set");

const selectClass =
  "block h-11 w-full rounded-lg border border-line-strong bg-surface px-3 text-[15px] text-ink shadow-card " +
  "focus:border-primary focus:outline-none focus:ring-[3px] focus:ring-primary/20";

function Choice({
  label,
  value,
  onChange,
  children,
  hint,
  required,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  children: ReactNode;
  hint?: ReactNode;
  required?: boolean;
}) {
  const id = useId();
  return (
    <FormField label={label} htmlFor={id} hint={hint} required={required}>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={selectClass}>
        {children}
      </select>
    </FormField>
  );
}

/* ------------------------------------------------------------------ status chips */

const STATUS: Record<string, { tone: Tone; label: string; icon: ReactNode }> = {
  reported: { tone: "warn", label: "Waiting for your care team", icon: <Clock3 className="h-3.5 w-3.5" aria-hidden /> },
  confirmed: { tone: "ok", label: "Confirmed", icon: <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> },
  stopped: { tone: "neutral", label: "Stopped", icon: null },
  resolved: { tone: "neutral", label: "Resolved", icon: null },
  dismissed: { tone: "neutral", label: "Not added", icon: null },
  open: { tone: "warn", label: "Open", icon: <Clock3 className="h-3.5 w-3.5" aria-hidden /> },
  in_progress: { tone: "info", label: "In progress", icon: <Clock3 className="h-3.5 w-3.5" aria-hidden /> },
  awaiting_hcp: { tone: "info", label: "Waiting for HCP", icon: <Stethoscope className="h-3.5 w-3.5" aria-hidden /> },
  hcp_responded: { tone: "warn", label: "HCP responded", icon: <MessageSquareText className="h-3.5 w-3.5" aria-hidden /> },
  closed: { tone: "ok", label: "Closed", icon: <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> },
};

export function StatusChip({ status, staff = false, label: given }: { status: string; staff?: boolean; label?: string }) {
  const s = STATUS[status] ?? { tone: "neutral" as Tone, label: titleCase(status), icon: null };
  const label = given ?? (staff && status === "reported" ? "Reported by patient" : s.label);
  return (
    <Badge tone={s.tone} icon={s.icon}>
      {label}
    </Badge>
  );
}

export const REQUEST_LABEL: Record<string, string> = {
  condition_review: "Condition to review",
  medication_review: "Medication to review",
  consultation: "Consultation request",
  follow_up: "Follow-up",
};

/* ------------------------------------------------------------------ forms */

export function ConditionForm({
  onSubmit,
  busy,
  error,
  submitLabel = "Add condition",
}: {
  onSubmit: (body: { condition: string; other_text: string | null }) => void;
  busy: boolean;
  error: unknown;
  submitLabel?: string;
}) {
  const vocab = useVocabulary();
  const [condition, setCondition] = useState("");
  const [other, setOther] = useState("");
  const [tried, setTried] = useState(false);
  const missing = !condition || (condition === "other" && !other.trim());
  function submit(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    if (!missing) onSubmit({ condition, other_text: condition === "other" ? other.trim() : null });
  }
  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <Choice label="Condition" value={condition} onChange={setCondition} required>
        <option value="">Choose a condition</option>
        {(vocab.data?.conditions ?? []).map((c) => (
          <option key={c.code} value={c.code}>
            {c.label}
          </option>
        ))}
      </Choice>
      {condition === "other" && (
        <TextField
          label="Name of the condition"
          value={other}
          maxLength={120}
          required
          onChange={(e) => setOther(e.target.value)}
          error={tried && !other.trim() ? "Enter the condition's name." : null}
        />
      )}
      {tried && !condition && <p className="text-[13px] text-bad">Choose a condition.</p>}
      <ErrorNote error={error} />
      <Button type="submit" variant="primary" busy={busy}>
        {submitLabel}
      </Button>
    </form>
  );
}

export interface MedicationValues {
  name: string;
  dose_instructions: string | null;
  schedule: string | null;
  start_date: string;
  end_date: string | null;
  /** Explicit: still being taken (no end date) or ending on `end_date`. */
  ongoing: boolean;
  /** When a supply was last collected, if known: so a medication already being taken is
   *  not mistaken for one that was never filled. */
  last_refill_date: string | null;
}

/** A medication. The patient reports what they take; the care team records a course,
 *  which may start later and end on a planned date. Dates are relative to the real date. */
export function MedicationForm({
  onSubmit,
  busy,
  error,
  extra,
  submitLabel = "Add medication",
  audience = "patient",
}: {
  onSubmit: (body: MedicationValues) => void;
  busy: boolean;
  error: unknown;
  /** Care-team fields (supply, copay), rendered after the patient fields. */
  extra?: ReactNode;
  submitLabel?: string;
  audience?: "patient" | "staff";
}) {
  const today = todayISO();
  const staff = audience === "staff";
  const vocab = useVocabulary();
  const listId = useId();
  const [v, setV] = useState({ name: "", dose_instructions: "", schedule: "", start_date: "", end_date: "", last_refill: "" });
  const [ongoing, setOngoing] = useState(true);
  const [tried, setTried] = useState(false);
  const errors = {
    name: !v.name.trim() ? "Enter the medication's name." : null,
    start_date: !v.start_date
      ? staff
        ? "Enter the start date."
        : "Enter when you started."
      : !staff && v.start_date > today
        ? "The start date cannot be in the future."
        : null,
    end_date: ongoing
      ? null
      : !v.end_date
        ? "Enter the end date, or mark it as ongoing."
        : v.start_date && v.end_date < v.start_date
          ? "The end date is before the start date."
          : null,
    last_refill:
      v.last_refill && ((v.start_date && v.last_refill < v.start_date) || v.last_refill > today)
        ? "Use a date between the start date and today."
        : null,
  };
  function submit(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    if (Object.values(errors).some(Boolean)) return;
    onSubmit({
      name: v.name.trim(),
      dose_instructions: v.dose_instructions.trim() || null,
      schedule: v.schedule.trim() || null,
      start_date: v.start_date,
      end_date: ongoing ? null : v.end_date || null,
      ongoing,
      last_refill_date: v.last_refill || null,
    });
  }
  const set = (key: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [key]: e.target.value });
  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <TextField
        label="Medication name"
        value={v.name}
        onChange={set("name")}
        list={listId}
        maxLength={64}
        required
        autoComplete="off"
        hint="Start typing to pick from the list, or enter any name."
        error={tried ? errors.name : null}
      />
      <datalist id={listId}>
        {(vocab.data?.medications ?? []).map((m) => (
          <option key={m.name} value={m.name} />
        ))}
      </datalist>
      <TextField
        label="Dose and instructions"
        value={v.dose_instructions}
        onChange={set("dose_instructions")}
        maxLength={300}
        placeholder="For example: 500 mg with dinner"
      />
      <TextField
        label="How often"
        value={v.schedule}
        onChange={set("schedule")}
        maxLength={64}
        placeholder="For example: once daily"
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Start date"
          type="date"
          max={staff ? addDays(today, 366) : today}
          value={v.start_date}
          onChange={set("start_date")}
          required
          hint={staff ? "Can be in the future for a course that starts later." : undefined}
          error={tried ? errors.start_date : null}
        />
        <TextField
          label="End date"
          type="date"
          min={v.start_date || undefined}
          max={addDays(today, 5 * 366)}
          value={ongoing ? "" : v.end_date}
          onChange={set("end_date")}
          disabled={ongoing}
          required={!ongoing}
          error={tried ? errors.end_date : null}
        />
      </div>
      {staff ? (
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-ink">Duration</legend>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="radio" name="duration" checked={ongoing} onChange={() => setOngoing(true)} className="h-4 w-4" />
            Ongoing (no end date)
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="radio" name="duration" checked={!ongoing} onChange={() => setOngoing(false)} className="h-4 w-4" />
            Ends on the end date
          </label>
        </fieldset>
      ) : (
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            aria-label="I am still taking it"
            checked={ongoing}
            onChange={(e) => setOngoing(e.target.checked)}
            className="h-4 w-4 accent-[var(--color-primary)]"
          />
          I am still taking it
        </label>
      )}
      <TextField
        label={staff ? "Last refill date (if already being taken)" : "When did you last collect a supply? (optional)"}
        type="date"
        min={v.start_date || undefined}
        max={today}
        value={v.last_refill}
        onChange={set("last_refill")}
        hint={
          staff
            ? "Recorded as the first refill, so adherence starts from what is already on hand."
            : "If you already take it, this tells your care team how much you have on hand."
        }
        error={tried ? errors.last_refill : null}
      />
      {extra}
      <ErrorNote error={error} />
      <Button type="submit" variant="primary" busy={busy}>
        {submitLabel}
      </Button>
    </form>
  );
}

/** "$" in the US, "£" in the UK, "€" in the euro area; otherwise none. */
export function currencyFor(country: string | null | undefined): string {
  if (country === "US") return "$";
  if (country === "GB") return "£";
  const euro = ["AT", "BE", "HR", "CY", "EE", "FI", "FR", "DE", "GR", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES"];
  return country && euro.includes(country) ? "€" : "";
}

/** Supply details only the care team sets (when recording or confirming a medication). */
export function SupplyFields({
  value,
  onChange,
  knownMeasure,
  currency = "",
}: {
  value: { measure: string; days_supply: string; copay: string };
  onChange: (v: { measure: string; days_supply: string; copay: string }) => void;
  knownMeasure?: string | null;
  /** The patient's currency symbol, when known. */
  currency?: string;
}) {
  const vocab = useVocabulary();
  return (
    <div className="grid gap-4 rounded-xl border border-line bg-subtle/50 p-4 sm:grid-cols-3">
      <Choice
        label="Adherence measure"
        value={value.measure}
        onChange={(measure) => onChange({ ...value, measure })}
        hint={knownMeasure ? `Recognised as ${measureLabel(knownMeasure).toLowerCase()}.` : "Needed for adherence tracking."}
      >
        <option value="">{knownMeasure ? `Keep (${measureLabel(knownMeasure)})` : "Not tracked"}</option>
        {(vocab.data?.measures ?? []).map((m) => (
          <option key={m} value={m}>
            {measureLabel(m)}
          </option>
        ))}
      </Choice>
      <Choice label="Supply per fill" value={value.days_supply} onChange={(days_supply) => onChange({ ...value, days_supply })} required>
        {(vocab.data?.days_supply ?? [30, 60, 90]).map((d) => (
          <option key={d} value={String(d)}>
            {d} days
          </option>
        ))}
      </Choice>
      <TextField
        label={currency ? `Copay (${currency})` : "Copay"}
        type="number"
        min={0}
        step="0.01"
        value={value.copay}
        onChange={(e) => onChange({ ...value, copay: e.target.value })}
      />
    </div>
  );
}

export function ConsultForm({
  onSubmit,
  busy,
  error,
}: {
  onSubmit: (reason: string) => void;
  busy: boolean;
  error: unknown;
}) {
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setTried(true);
        if (reason.trim()) onSubmit(reason.trim());
      }}
      className="space-y-4"
      noValidate
    >
      <TextArea
        label="What would you like to discuss?"
        rows={4}
        maxLength={500}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        required
        hint="Your care manager reads this and arranges the right healthcare professional."
        error={tried && !reason.trim() ? "Tell your care team what it is about." : null}
      />
      <ErrorNote error={error} />
      <Button type="submit" variant="primary" busy={busy}>
        Send request
      </Button>
    </form>
  );
}

/* ------------------------------------------------------------------ HCP specialties */

/** "Cardiology, Internal Medicine (general medicine)" or the explicit "not configured". */
export function specialtyText(list: Json[] | null | undefined) {
  return list?.length ? list.map((s) => s.label).join(", ") : "Specialty not configured";
}

export function SpecialtyChips({ list }: { list: Json[] | null | undefined }) {
  if (!list?.length) return <span className="text-sm italic text-ink-subtle">Specialty not configured</span>;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {list.map((s) => (
        <li key={s.code}>
          <Badge tone="info">{s.label}</Badge>
        </li>
      ))}
    </ul>
  );
}

export interface SpecialtyOption {
  code: string;
  label: string;
}

export const useSpecialtyOptions = () =>
  useQuery({ queryKey: ["specialty-options"], queryFn: () => api<SpecialtyOption[]>("/care/specialties"), staleTime: Infinity });

/** Checkbox group over the controlled list. Nothing is preselected. */
export function SpecialtyPicker({
  value,
  onChange,
  legend = "Specialties",
  hint,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  legend?: string;
  hint?: ReactNode;
}) {
  const options = useSpecialtyOptions();
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium text-ink">{legend}</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {(options.data ?? []).map((o) => (
          <label key={o.code} className="flex min-h-10 items-center gap-2 rounded-lg border border-line px-3 text-sm text-ink">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={value.includes(o.code)}
              onChange={(e) => onChange(e.target.checked ? [...value, o.code] : value.filter((v) => v !== o.code))}
            />
            {o.label}
          </label>
        ))}
      </div>
      {hint && <p className="text-[13px] text-ink-subtle">{hint}</p>}
    </fieldset>
  );
}

/* ------------------------------------------------------------------ read-only lists */

export function ConditionList({ items, staff, actions }: { items: Json[]; staff?: boolean; actions?: (c: Json) => ReactNode }) {
  if (!items.length) return <p className="text-sm text-ink-subtle">No conditions recorded.</p>;
  return (
    <ul className="divide-y divide-line">
      {items.map((c) => (
        <li key={c.id} className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-subtle text-ink-muted" aria-hidden>
              <HeartPulse className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <div className="text-sm font-semibold text-ink">{c.label}</div>
              <div className="text-[13px] text-ink-subtle">
                {c.origin === "patient_reported" ? "Added by the patient" : "Recorded by the care team"} ·{" "}
                {fmtDate(c.reported_at)}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusChip status={c.status} staff={staff} />
            {actions?.(c)}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function MedicationSummary({ m, staff }: { m: Json; staff?: boolean }) {
  return (
    <div className="flex min-w-0 items-start gap-3">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-subtle text-ink-muted" aria-hidden>
        <Pill className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-ink">{titleCase(m.drug_name)}</span>
          <StatusChip status={m.review_status} staff={staff} />
        </div>
        <div className="mt-0.5 text-[13px] leading-5 text-ink-subtle">
          {[m.dose_instructions, m.schedule].filter(Boolean).join(" · ") || "No dose recorded"}
        </div>
        <div className="text-[13px] leading-5 text-ink-subtle">
          Since {fmtDate(m.start_date)}
          {m.end_date ? ` until ${fmtDate(m.end_date)}` : ""}
          {m.days_supply ? ` · ${m.days_supply}-day supply` : ""}
          {m.prescriber ? ` · ${m.prescriber}` : ""}
        </div>
      </div>
    </div>
  );
}

export function CareTeam({ team }: { team: Json }) {
  const hcps: Json[] = team?.hcps ?? [];
  const managers: Json[] = team?.care_managers ?? [];
  return (
    <ul className="space-y-3">
      {!managers.length && (
        <li className="flex items-center gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-subtle text-ink-subtle" aria-hidden>
            <Clock3 className="h-4 w-4" />
          </span>
          <div className="text-sm text-ink-muted">A care manager is being assigned.</div>
        </li>
      )}
      {managers.map((m) => (
        <li key={`cm-${m.id}`} className="flex items-center gap-3">
          <Avatar name={m.name} size="sm" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-ink">{m.name}</div>
            <div className="flex items-center gap-1 text-[13px] text-ink-subtle">
              <UserRound className="h-3.5 w-3.5" aria-hidden /> Care manager
            </div>
          </div>
        </li>
      ))}
      {hcps.map((h) => (
        <li key={h.hcp_id} className="flex items-center gap-3">
          <Avatar name={h.name} size="sm" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-ink">{h.name}</div>
            <div className="flex items-center gap-1 truncate text-[13px] text-ink-subtle">
              <Stethoscope className="h-3.5 w-3.5 shrink-0" aria-hidden /> {specialtyText(h.specialties)}
            </div>
          </div>
          {h.available === false ? (
            <Badge tone="neutral">No longer available</Badge>
          ) : (
            h.is_primary && <Badge tone="sage">Primary</Badge>
          )}
        </li>
      ))}
    </ul>
  );
}

export function RequestList({ items, staff, actions }: { items: Json[]; staff?: boolean; actions?: (r: Json) => ReactNode }) {
  if (!items.length) return <p className="text-sm text-ink-subtle">No requests.</p>;
  return (
    <ul className="divide-y divide-line">
      {items.map((r) => (
        <li key={r.id} className="py-3 first:pt-0 last:pb-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-semibold text-ink">{REQUEST_LABEL[r.type] ?? titleCase(r.type)}</span>
            <span className="flex flex-wrap items-center gap-1.5">
              {r.overdue && <Badge tone="bad">Overdue</Badge>}
              {/* Patients read what the status means for them; staff see the workflow state. */}
              <StatusChip status={r.status} staff={staff} label={staff ? undefined : r.status_label} />
            </span>
          </div>
          <div className="mt-0.5 text-[13px] leading-5 text-ink-subtle">
            {r.condition?.label ?? r.medication ?? ""}
            {r.condition || r.medication ? " · " : ""}
            {r.type === "follow_up" && r.due_date ? `Due ${fmtDate(r.due_date)} · ` : ""}
            {staff && r.owner ? `Owner: ${r.owner} · ` : ""}
            {fmtDate(r.created_at)}
          </div>
          {r.reason && (r.type === "consultation" || r.type === "follow_up") && (
            <p className="mt-1 whitespace-pre-line text-sm text-ink-muted">
              {r.type === "consultation" ? `“${r.reason}”` : r.reason}
            </p>
          )}
          {r.assigned_hcp && (
            <p className="mt-1 text-[13px] text-ink-muted">
              {r.status === "awaiting_hcp" ? "Waiting for " : r.status === "hcp_responded" ? "Answered by " : "Routed to "}
              <span className="font-semibold text-ink">{r.assigned_hcp.name}</span> ({specialtyText(r.assigned_hcp.specialties)})
              {staff && r.status === "awaiting_hcp" && (
                <span className="text-ink-subtle">
                  {r.assigned_hcp.in_app ? " · answers in the app" : " · does not use the app: record their response"}
                </span>
              )}
            </p>
          )}
          {r.resolution && !r.resolution.startsWith("Routed to") && (
            <p className="mt-1 text-[13px] text-ink-muted">{r.resolution}</p>
          )}
          {r.status === "awaiting_hcp" && !staff && (
            <p className="mt-1 text-[13px] text-ink-muted">
              Your care manager has passed this on. You will see the answer here; there is nothing you need to do now.
            </p>
          )}
          {(r.notes ?? []).length > 0 && (
            <div className="mt-2">
              <NoteList items={r.notes} staff={staff} compact />
            </div>
          )}
          {actions && <div className="mt-2 flex flex-wrap gap-2">{actions(r)}</div>}
        </li>
      ))}
    </ul>
  );
}

export function NoteList({ items, staff, compact }: { items: Json[]; staff?: boolean; compact?: boolean }) {
  if (!items.length) return <p className="text-sm text-ink-subtle">No instructions yet.</p>;
  return (
    <ul className="space-y-3">
      {items.map((n) => (
        <li key={n.id} className={cx("rounded-lg border border-line p-3", n.kind === "hcp_instruction" && "bg-primary-soft/40")}>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-ink-subtle">
            <MessageSquareText className="h-3.5 w-3.5" aria-hidden />
            <span className="font-semibold text-ink">{n.kind === "hcp_instruction" ? "Instruction" : "Follow-up"}</span>
            {n.hcp && <span>from {n.hcp}{n.by_hcp ? "" : staff ? " (recorded by the care team)" : ""}</span>}
            <span aria-hidden>·</span>
            <span className="tabular">{fmtDateTime(n.created_at)}</span>
            {staff && !n.visible_to_patient && <Badge tone="neutral">Care team only</Badge>}
          </div>
          {n.request && !compact && (
            <p className="mt-1 text-[13px] text-ink-subtle">
              About: {REQUEST_LABEL[n.request.type] ?? titleCase(n.request.type)} of {fmtDate(n.request.created_at)}
              {n.request.reason ? ` · “${n.request.reason.length > 80 ? `${n.request.reason.slice(0, 80)}…` : n.request.reason}”` : ""}
            </p>
          )}
          <p className="mt-1.5 whitespace-pre-line text-sm leading-6 text-ink">{n.text}</p>
        </li>
      ))}
    </ul>
  );
}
