import { Steps } from "../charts";
import type { Step } from "../charts";
import { DUR, Morph, gsap, reducedMotion, useGSAP } from "../motion";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  FilePen,
  GitBranch,
  MessageSquare,
  Plus,
  Send,
  ShieldAlert,
  Trash2,
  Undo2,
  XCircle,
} from "lucide-react";
import { useEffect, useState, useRef } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, patch, post } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { P } from "../permissions";
import { useToast } from "../toast";
import {
  Alert,
  Badge,
  Button,
  Card,
  ChannelIcon,
  EmptyState,
  ErrorNote,
  ErrorState,
  KpiGrid,
  Loading,
  MlrBadge,
  PageHeader,
  Stat,
  TextArea,
  TextField,
  Timeline,
  TimelineItem,
  channelName,
  cx,
  fmtDate,
  fmtDateTime,
  num,
  titleCase,
} from "../ui";

const DECISION: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "neutral" | "brand" }> = {
  approve: { label: "Approved", tone: "ok" },
  request_changes: { label: "Changes requested", tone: "warn" },
  reject: { label: "Rejected", tone: "bad" },
  withdraw: { label: "Withdrawn", tone: "bad" },
  request_revision: { label: "Revision requested", tone: "warn" },
};
const PERSPECTIVES = [
  { key: "medical", label: "Medical", hint: "Accuracy, claim support, benefits and risks, safety" },
  { key: "legal", label: "Legal", hint: "Misleading wording, legal risk, disclaimers" },
  { key: "regulatory", label: "Regulatory", hint: "Indication, approved use, labelling, jurisdiction" },
] as const;
const FIELD_LABEL: Record<string, string> = {
  title: "Title",
  body: "HCP-facing wording",
  claims: "Claims and references",
  indication: "Indication",
  safety_info: "Safety and benefit-risk",
  labelling_note: "Labelling",
  jurisdictions: "Where it may be used",
  channels: "Channels",
  specialty: "Specialty",
  measure: "Therapy area",
  action_type: "Kind of material",
  product: "Product or medicine",
};
const OUTCOME_LABEL: Record<string, string> = {
  pending: "Delivered, no response yet",
  opened: "Opened",
  clicked: "Clicked",
  replied: "Replied",
  completed: "Completed",
  no_response: "No response",
  declined: "Declined",
};

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) {
    return value.map((v) => (typeof v === "object" && v ? `${(v as Json).text} [${(v as Json).reference || "no reference"}]` : String(v))).join("; ");
  }
  return String(value);
}

/* ------------------------------------------------------------------ proposal form */

type Form = {
  product: string;
  title: string;
  body: string;
  action_type: string;
  channels: string[];
  measure: string;
  specialty: string;
  claims: Array<{ text: string; reference: string }>;
  indication: string;
  safety_info: string;
  labelling_note: string;
  jurisdictions: string[];
};

function toForm(c?: Json): Form {
  return {
    product: c?.product ?? "",
    title: c?.title ?? "",
    body: c?.body ?? "",
    action_type: c?.action_type ?? "hcp_education",
    channels: c?.channels ?? ["email", "portal"],
    measure: c?.measure ?? "",
    specialty: c?.specialty ?? "",
    claims: c?.claims?.length ? c.claims : [{ text: "", reference: "" }],
    indication: c?.indication ?? "",
    safety_info: c?.safety_info ?? "",
    labelling_note: c?.labelling_note ?? "",
    jurisdictions: c?.jurisdictions ?? [],
  };
}

function ContentForm({ initial, library, onSave, saving, onCancel }: {
  initial?: Json;
  library?: boolean;
  onSave: (body: Json) => void;
  saving: boolean;
  onCancel: () => void;
}) {
  const [f, setF] = useState<Form>(() => toForm(initial));
  const options = useQuery({ queryKey: ["content-options"], queryFn: () => api("/content/options") });
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => ({ ...x, [k]: v }));
  const toggle = (k: "channels" | "jurisdictions", v: string) =>
    set(k, f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v]);
  const o = options.data;
  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({
          ...f,
          product: f.product || null,
          measure: f.measure || null,
          specialty: f.specialty || null,
          jurisdictions: f.jurisdictions.length ? f.jurisdictions : null,
          claims: f.claims.filter((c) => c.text.trim() || c.reference.trim()),
          indication: f.indication || null,
          safety_info: f.safety_info || null,
          labelling_note: f.labelling_note || null,
        });
      }}
    >
      <TextField label="Title" required value={f.title} onChange={(e) => set("title", e.target.value)} maxLength={200} />
      <TextField
        label="Product or medicine (optional)"
        hint="What the material is about, as named in the approved labelling."
        value={f.product}
        onChange={(e) => set("product", e.target.value)}
        maxLength={120}
      />
      <TextArea
        label="HCP-facing wording"
        hint="Exactly what the HCP will read. After approval nobody can change it; a change is a new version reviewed again. No links or placeholders."
        required
        rows={5}
        value={f.body}
        onChange={(e) => set("body", e.target.value)}
        maxLength={2000}
      />
      {!library && o && (
        <div className="grid gap-4 sm:grid-cols-3">
          <label className="text-sm font-medium text-ink">
            Kind of material
            <select className="mt-1.5 h-11 w-full rounded-lg border border-line bg-surface px-3 text-sm" value={f.action_type} onChange={(e) => set("action_type", e.target.value)}>
              {o.action_types.map((a: Json) => (
                <option key={a.code} value={a.code}>{a.label}</option>
              ))}
            </select>
          </label>
          <label className="text-sm font-medium text-ink">
            Therapy area
            <select className="mt-1.5 h-11 w-full rounded-lg border border-line bg-surface px-3 text-sm" value={f.measure} onChange={(e) => set("measure", e.target.value)}>
              <option value="">General (any)</option>
              {o.measures.map((m: Json) => (
                <option key={m.code} value={m.code}>{m.label}</option>
              ))}
            </select>
          </label>
          <label className="text-sm font-medium text-ink">
            Specialty
            <select className="mt-1.5 h-11 w-full rounded-lg border border-line bg-surface px-3 text-sm" value={f.specialty} onChange={(e) => set("specialty", e.target.value)}>
              <option value="">Any specialty</option>
              {o.specialties.map((s: Json) => (
                <option key={s.code} value={s.code}>{s.label}</option>
              ))}
            </select>
          </label>
        </div>
      )}
      {!library && o && (
        <fieldset>
          <legend className="text-sm font-medium text-ink">Channels</legend>
          <div className="mt-2 flex flex-wrap gap-4">
            {o.channels.map((c: Json) => (
              <label key={c.code} className="inline-flex items-center gap-2 text-sm">
                <input type="checkbox" checked={f.channels.includes(c.code)} onChange={() => toggle("channels", c.code)} /> {c.label}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <fieldset className="space-y-3">
        <legend className="text-sm font-medium text-ink">Claims and supporting references</legend>
        <p className="text-[13px] text-ink-subtle">Every claim the material makes, each with the evidence that supports it.</p>
        {f.claims.map((c, n) => (
          <div key={n} className="grid gap-2 rounded-xl border border-line p-3 sm:grid-cols-[1fr_1fr_auto]">
            <TextField label={`Claim ${n + 1}`} value={c.text} maxLength={500} onChange={(e) => set("claims", f.claims.map((x, i) => (i === n ? { ...x, text: e.target.value } : x)))} />
            <TextField label="Reference" value={c.reference} maxLength={500} onChange={(e) => set("claims", f.claims.map((x, i) => (i === n ? { ...x, reference: e.target.value } : x)))} />
            <Button variant="ghost" className="self-end" aria-label={`Remove claim ${n + 1}`} onClick={() => set("claims", f.claims.filter((_, i) => i !== n))}>
              <Trash2 className="h-4 w-4" aria-hidden />
            </Button>
          </div>
        ))}
        <Button variant="secondary" size="sm" onClick={() => set("claims", [...f.claims, { text: "", reference: "" }])}>
          <Plus className="h-4 w-4" aria-hidden /> Add a claim
        </Button>
      </fieldset>
      <TextArea label="Indication" hint="Who the material is about: the approved use it relates to." rows={2} value={f.indication} onChange={(e) => set("indication", e.target.value)} />
      <TextArea label="Safety and benefit-risk information" rows={3} value={f.safety_info} onChange={(e) => set("safety_info", e.target.value)} />
      <TextArea label="Labelling note (optional)" rows={2} value={f.labelling_note} onChange={(e) => set("labelling_note", e.target.value)} />
      {o && (
        <fieldset>
          <legend className="text-sm font-medium text-ink">Where it may be used</legend>
          <p className="text-[13px] text-ink-subtle">Leave all unticked for no country restriction. HCPs elsewhere will not receive it.</p>
          <div className="mt-2 grid max-h-44 grid-cols-2 gap-x-4 gap-y-1 overflow-y-auto rounded-lg border border-line p-3 sm:grid-cols-3">
            {o.countries.map((c: Json) => (
              <label key={c.code} className="inline-flex items-center gap-2 text-sm">
                <input type="checkbox" checked={f.jurisdictions.includes(c.code)} onChange={() => toggle("jurisdictions", c.code)} /> {c.name}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" type="submit" busy={saving}>
          Save draft
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

export function NewContent() {
  const navigate = useNavigate();
  const toast = useToast();
  const client = useQueryClient();
  const create = useMutation({
    mutationFn: (body: Json) => post("/content", body),
    onSuccess: (c: Json) => {
      toast(`Draft ${c.content_id} saved. Submit it for MLR review when it is complete.`);
      void client.invalidateQueries({ queryKey: ["content"] });
      navigate(`/content/${c.content_id}`, { replace: true });
    },
  });
  return (
    <>
      <PageHeader back title="New content proposal" subtitle="Draft HCP material with its claims, evidence, indication and safety information. MLR reviews it once you submit." />
      <Card>
        <ErrorNote error={create.error} className="mb-4" />
        <ContentForm onSave={(b) => create.mutate(b)} saving={create.isPending} onCancel={() => navigate("/content")} />
      </Card>
    </>
  );
}

/* ------------------------------------------------------------------ MLR decision panel */

function DecisionPanel({ c, onDone }: { c: Json; onDone: (data: Json) => void }) {
  const [views, setViews] = useState<Record<string, { verdict: string; note: string }>>({});
  const [feedback, setFeedback] = useState("");
  const [comment, setComment] = useState("");
  const [confirmWithdraw, setConfirmWithdraw] = useState(false);
  const decide = useMutation({
    mutationFn: (v: { decision: string; take_over?: boolean }) =>
      post(`/content/${c.content_id}/review`, {
        decision: v.decision,
        expected_status: c.mlr_status,
        expected_version: c.version,
        take_over: Boolean(v.take_over),
        feedback: feedback || null,
        comment: comment || null,
        ...(c.mlr_status === "pending"
          ? Object.fromEntries(PERSPECTIVES.map((p) => [p.key, views[p.key] ?? null]))
          : {}),
      }),
    onSuccess: onDone,
  });
  const claim = useMutation({
    mutationFn: (take_over: boolean) => post(`/content/${c.content_id}/claim-review`, { take_over }),
    onSuccess: onDone,
  });
  const { user } = useAuth();
  const allowed: string[] = c.actions.decide;
  if (!allowed.length) return null;
  const owner = c.review_owner_user_id;
  const ownedByOther = c.mlr_status === "pending" && owner && owner !== user?.id;
  const pending = c.mlr_status === "pending";
  const allOk = PERSPECTIVES.every((p) => views[p.key]?.verdict === "ok");
  return (
    <Card title={pending ? "Your review" : "Approved material"} description={pending ? "Record each perspective, then decide. The author reads the feedback, not the internal note." : "Withdraw it if it must stop, or ask the author for a new version while it stays in use."}>
      {pending && !owner && (
        <Alert tone="info" className="mb-4" title="Not yet started">
          <Button size="sm" variant="secondary" busy={claim.isPending} onClick={() => claim.mutate(false)}>
            Start review
          </Button>
        </Alert>
      )}
      {ownedByOther && (
        <Alert tone="warn" className="mb-4" title={`Reviewer #${owner} is reviewing this`}>
          Deciding takes the review over; both are kept in the audit.
        </Alert>
      )}
      <ErrorNote error={decide.error ?? claim.error} className="mb-4" />
      {pending && (
        <div className="space-y-4">
          {PERSPECTIVES.map((p) => (
            <fieldset key={p.key} className="rounded-xl border border-line p-4">
              <legend className="px-1 text-sm font-semibold text-ink">{p.label} review</legend>
              <p className="text-[13px] text-ink-subtle">{p.hint}</p>
              <div className="mt-2 flex flex-wrap gap-4 text-sm">
                {(["ok", "concern"] as const).map((v) => (
                  <label key={v} className="inline-flex items-center gap-2">
                    <input
                      type="radio"
                      name={`${p.key}-verdict`}
                      checked={views[p.key]?.verdict === v}
                      onChange={() => setViews((x) => ({ ...x, [p.key]: { verdict: v, note: x[p.key]?.note ?? "" } }))}
                    />
                    {v === "ok" ? "No concern" : "Concern"}
                  </label>
                ))}
              </div>
              <TextField
                label={`${p.label} note`}
                className="mt-2"
                value={views[p.key]?.note ?? ""}
                maxLength={1000}
                onChange={(e) => setViews((x) => ({ ...x, [p.key]: { verdict: x[p.key]?.verdict ?? "concern", note: e.target.value } }))}
              />
            </fieldset>
          ))}
        </div>
      )}
      <TextArea
        className="mt-4"
        label={pending ? "Feedback to the author" : "Reason (shown to the author and to recipients of a withdrawal)"}
        hint={pending ? "Required to request changes or reject: say what is wrong and what to change." : "Required."}
        rows={3}
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
      />
      <TextArea className="mt-4" label="Internal note (Compliance only, optional)" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
      <div className="mt-4 flex flex-wrap gap-2">
        {allowed.includes("approve") && (
          <Button variant="primary" disabled={!allOk} busy={decide.isPending} onClick={() => decide.mutate({ decision: "approve", take_over: Boolean(ownedByOther) })}>
            <CheckCircle2 className="h-4 w-4" aria-hidden /> Approve version {c.version}
          </Button>
        )}
        {allowed.includes("request_changes") && (
          <Button variant="secondary" disabled={!feedback.trim()} busy={decide.isPending} onClick={() => decide.mutate({ decision: "request_changes", take_over: Boolean(ownedByOther) })}>
            <Undo2 className="h-4 w-4" aria-hidden /> Request changes
          </Button>
        )}
        {allowed.includes("reject") && (
          <Button variant="quiet-danger" disabled={!feedback.trim()} busy={decide.isPending} onClick={() => decide.mutate({ decision: "reject", take_over: Boolean(ownedByOther) })}>
            <XCircle className="h-4 w-4" aria-hidden /> Reject
          </Button>
        )}
        {allowed.includes("request_revision") && (
          <Button variant="secondary" disabled={!feedback.trim()} busy={decide.isPending} onClick={() => decide.mutate({ decision: "request_revision" })}>
            <Undo2 className="h-4 w-4" aria-hidden /> Ask the author for a new version
          </Button>
        )}
        {allowed.includes("withdraw") && !confirmWithdraw && (
          <Button variant="quiet-danger" disabled={!feedback.trim()} onClick={() => setConfirmWithdraw(true)}>
            <ShieldAlert className="h-4 w-4" aria-hidden /> Withdraw
          </Button>
        )}
      </div>
      {confirmWithdraw && (
        <Alert tone="bad" className="mt-4" title={`Withdraw ${c.content_id} version ${c.version}?`}>
          <p>Open recommendations that would deliver it stop now, and recipients see it marked withdrawn with your reason.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="quiet-danger" busy={decide.isPending} onClick={() => decide.mutate({ decision: "withdraw" })}>
              Confirm withdrawal
            </Button>
            <Button variant="ghost" onClick={() => setConfirmWithdraw(false)}>
              Keep it in use
            </Button>
          </div>
        </Alert>
      )}
      <div>
      </div>
      {pending && !allOk && (
        <p className="mt-2 text-[13px] text-ink-subtle">Approval needs all three perspectives recorded with no concern.</p>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ conversation */

function MessageBox({ placeholder, onSend, busy }: { placeholder: string; onSend: (text: string) => Promise<unknown>; busy: boolean }) {
  const [text, setText] = useState("");
  return (
    <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
      <TextArea label={placeholder} className="flex-1" rows={2} value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} />
      <Button variant="secondary" busy={busy} disabled={!text.trim()} onClick={() => void onSend(text).then(() => setText(""))}>
        <Send className="h-4 w-4" aria-hidden /> Send
      </Button>
    </div>
  );
}

const VERSION_DECISION: Record<string, string> = {
  approve: "Approved",
  request_changes: "Changes requested",
  reject: "Rejected",
  withdraw: "Withdrawn",
  request_revision: "New version requested",
};

/** The lineage as a path: each version, then the decisions taken on it, so a reviewer sees
 *  how the material reached its current state. Every step carries its status in words. */
function VersionTimeline({ versions, reviews, current }: { versions: Json[]; reviews: Json[]; current: string }) {
  const ordered = [...versions].sort((a, b) => a.version - b.version);
  const ref = useRef<HTMLOListElement>(null);
  // Once, on opening: the lineage line draws down and each version appears in turn.
  useGSAP(
    () => {
      if (reducedMotion()) return;
      const tl = gsap.timeline({ defaults: { duration: DUR.standard } });
      tl.from("[data-vt-line]", { scaleY: 0, transformOrigin: "50% 0%", duration: 0.5, ease: "power2.inOut" }).from(
        "[data-vt-item]",
        { opacity: 0, x: -6, stagger: 0.1, clearProps: "opacity,transform" },
        0.1,
      );
    },
    { scope: ref },
  );
  return (
    <ol ref={ref} className="relative space-y-4" aria-label="Version history">
      <span data-vt-line aria-hidden className="absolute inset-y-2 left-[11px] w-0.5 bg-line" />
      {ordered.map((v) => {
        const decisions = reviews.filter((r) => r.content_id === v.content_id && r.kind === "review");
        const here = v.content_id === current;
        return (
          <li key={v.content_id} data-vt-item className="relative pl-9">
            <span
              className={cx(
                "absolute left-0 top-0.5 grid h-6 w-6 place-items-center rounded-full text-[11px] font-semibold",
                here ? "bg-primary text-on-primary ring-4 ring-primary-soft" : "border-2 border-line-strong bg-surface text-ink-muted",
              )}
              aria-hidden
            >
              {v.version}
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <Link to={`/content/${v.content_id}`} className={cx("text-sm font-semibold hover:underline", here ? "text-ink" : "text-primary-ink")}>
                Version {v.version} · {v.content_id}
              </Link>
              <Morph value={v.status}>
                <MlrBadge status={v.status} />
              </Morph>
              {here && <span className="text-xs text-ink-subtle">(this page)</span>}
            </div>
            <p className="tabular mt-0.5 text-xs text-ink-subtle">
              {[
                v.submitted_at && `Submitted ${fmtDate(v.submitted_at)}`,
                v.decided_at && `Decided ${fmtDate(v.decided_at)}`,
                v.expiry_date && v.status === "approved" && `Valid until ${fmtDate(v.expiry_date)}`,
              ]
                .filter(Boolean)
                .join(" · ") || "Not submitted"}
            </p>
            {decisions.length > 0 && (
              <ul className="mt-1.5 space-y-1">
                {decisions.map((r) => (
                  <li key={r.id} className="flex flex-wrap items-baseline gap-x-2 text-[13px]">
                    <span className="font-semibold text-ink">{VERSION_DECISION[r.decision] ?? r.decision}</span>
                    <span className="tabular text-xs text-ink-subtle">{fmtDate(r.ts)}</span>
                    {r.feedback && <span className="line-clamp-1 min-w-0 text-ink-muted">“{r.feedback}”</span>}
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * For the representative who proposed it: where this material is on its way to an HCP.
 * Built only from the record (submission, decisions, approval window, deliveries); a step
 * that has not happened is shown as ahead, never assumed.
 */
function ContentLifecycle({ c }: { c: Json }) {
  const decided = (decision: string) => (c.reviews ?? []).some((r: Json) => r.kind === "review" && r.decision === decision);
  const status: string = c.mlr_status;
  const ended = status === "rejected" || status === "withdrawn";
  const approved = status === "approved" || status === "superseded";
  const steps: Step[] = [];
  const push = (key: string, label: string, done: boolean, date?: string | null) => steps.push({ key, label, state: done ? "done" : "todo", date });
  push("draft", "Drafted", true);
  push("review", "MLR review", Boolean(c.decided_at) || approved || ended, c.submitted_at);
  if (decided("request_changes")) push("changes", "Changes requested", true);
  if ((c.versions ?? []).length > 1) push("resubmitted", "Resubmitted", c.version > 1 && Boolean(c.submitted_at));
  if (ended) {
    push("ended", status === "rejected" ? "Rejected" : "Withdrawn", true, c.decided_at);
  } else {
    push("approved", "Approved", approved, approved ? c.decided_at : null);
    push("ready", "Ready to use", Boolean(c.usable), c.usable ? c.effective_date : null);
    push("sent", "Sent to your HCPs", Boolean(c.delivered_by_you));
  }
  // The first step not done is the current one (unless the path ended).
  const next = steps.findIndex((s) => s.state === "todo");
  if (next >= 0 && !ended) steps[next] = { ...steps[next], state: "current" };
  return (
    <Card title="Where this material is" className="mb-4">
      <Steps steps={steps} label="Content lifecycle" seenKey={`content.${c.lineage_id ?? c.content_id}.${c.content_id}`} />
    </Card>
  );
}

function History({ c, onPosted }: { c: Json; onPosted: (d: Json) => void }) {
  const send = useMutation({
    mutationFn: (v: { body: string; interaction_id?: number }) => post(`/content/${c.content_id}/messages`, v),
    onSuccess: (d: Json) => {
      if (d?.content_id) onPosted(d);
    },
  });
  const events = [...c.reviews, ...c.conversation.general].sort((a: Json, b: Json) => String(a.ts).localeCompare(String(b.ts)));
  return (
    <>
      <Card title="Review history and conversation" description="Every decision on every version, and messages between the author and MLR.">
        {!events.length ? (
          <p className="text-sm text-ink-subtle">No decisions or messages yet.</p>
        ) : (
          <Timeline>
            {events.map((e: Json) =>
              e.kind === "review" ? (
                <TimelineItem
                  key={`r${e.id}`}
                  tone={DECISION[e.decision]?.tone ?? "neutral"}
                  title={
                    <span>
                      <strong>{DECISION[e.decision]?.label ?? e.decision}</strong> · version {e.version} · {e.reviewer}
                    </span>
                  }
                  meta={fmtDateTime(e.ts)}
                >
                  {(e.medical || e.legal || e.regulatory) && (
                    <ul className="mt-1.5 flex flex-wrap gap-1.5">
                      {PERSPECTIVES.map((p) =>
                        e[p.key] ? (
                          <li key={p.key}>
                            <Badge tone={e[p.key].verdict === "ok" ? "ok" : "warn"}>
                              {p.label}: {e[p.key].verdict === "ok" ? "no concern" : "concern"}
                              {e[p.key].note ? ` · ${e[p.key].note}` : ""}
                            </Badge>
                          </li>
                        ) : null,
                      )}
                    </ul>
                  )}
                  {e.feedback && <p className="mt-1.5 whitespace-pre-line text-sm text-ink">{e.feedback}</p>}
                  {e.comment && <p className="mt-1 text-[13px] text-ink-subtle">Internal note: {e.comment}</p>}
                </TimelineItem>
              ) : (
                <TimelineItem key={`m${e.id}`} icon={<MessageSquare className="h-3.5 w-3.5" aria-hidden />} title={<strong>{e.author}</strong>} meta={fmtDateTime(e.ts)}>
                  <p className="mt-1 whitespace-pre-line text-sm text-ink">{e.body}</p>
                </TimelineItem>
              ),
            )}
          </Timeline>
        )}
        {c.actions.message && (
          <MessageBox placeholder="Message about this content" busy={send.isPending} onSend={(body) => send.mutateAsync({ body })} />
        )}
        <ErrorNote error={send.error} className="mt-2" />
      </Card>
      {c.conversation.hcp_threads.length > 0 && (
        <Card title="Questions from HCPs" description="Asked about material delivered to them. Compliance sees the HCP without identity; the representative who sent it sees the name.">
          <div className="space-y-5">
            {c.conversation.hcp_threads.map((t: Json) => (
              <div key={t.interaction_id} className="rounded-xl border border-line p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                  {t.hcp?.label ?? "HCP"}
                  {t.hcp?.specialties?.length > 0 && <Badge>{t.hcp.specialties.join(", ")}</Badge>}
                  {t.hcp?.country && <Badge tone="sage">{t.hcp.country}</Badge>}
                </div>
                <ul className="space-y-2">
                  {t.messages.map((m: Json) => (
                    <li key={m.id} className="text-sm">
                      <span className="font-semibold">{m.author}</span> <span className="text-[12px] text-ink-subtle">{fmtDateTime(m.ts)}</span>
                      <p className="whitespace-pre-line text-ink">{m.body}</p>
                    </li>
                  ))}
                </ul>
                {t.interaction_id && (
                  <MessageBox placeholder="Reply to this HCP" busy={send.isPending} onSend={(body) => send.mutateAsync({ body, interaction_id: t.interaction_id })} />
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </>
  );
}

function Deliveries({ d }: { d: Json }) {
  const [showWording, setShowWording] = useState(false);
  return (
    <Card title="Delivery and feedback" description="What happened to this material once approved: every version, recipients without identity, the exact wording delivered, and the response.">
      <KpiGrid>
        <Stat label="Delivered" value={num(d.sent)} />
        <Stat label="Opened or clicked" value={num((d.outcomes.opened ?? 0) + (d.outcomes.clicked ?? 0) + (d.outcomes.replied ?? 0))} />
        <Stat label="Waiting to send" value={num((d.recommendations.ready_for_review ?? 0) + (d.recommendations.approved ?? 0))} hint="recommendations open" />
        <Stat label="Copies after withdrawal" value={num(d.still_in_inboxes_after_withdrawal)} hint="marked withdrawn for recipients" tone={d.still_in_inboxes_after_withdrawal ? "warn" : undefined} />
      </KpiGrid>
      {!d.items.length ? (
        <p className="text-sm text-ink-subtle">Not delivered yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead className="text-[12px] uppercase tracking-wide text-ink-subtle">
              <tr>
                <th className="py-2 pr-3">Recipient</th>
                <th className="py-2 pr-3">Version</th>
                <th className="py-2 pr-3">Channel</th>
                <th className="py-2 pr-3">Delivered</th>
                <th className="py-2">Response</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {d.items.map((i: Json) => (
                <tr key={i.interaction_id}>
                  <td className="py-2 pr-3">
                    {i.recipient.label}
                    {i.recipient.specialties?.length ? <span className="text-ink-subtle"> · {i.recipient.specialties.join(", ")}</span> : null}
                    {i.recipient.country ? <span className="text-ink-subtle"> · {i.recipient.country}</span> : null}
                  </td>
                  <td className="py-2 pr-3 tabular">v{i.version}</td>
                  <td className="py-2 pr-3">
                    <span className="inline-flex items-center gap-1"><ChannelIcon channel={i.channel} className="h-3.5 w-3.5" /> {channelName(i.channel)}</span>
                  </td>
                  <td className="py-2 pr-3 tabular">{fmtDate(i.sent_at)}</td>
                  <td className="py-2">{OUTCOME_LABEL[i.outcome] ?? titleCase(i.outcome)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {d.wording.length > 0 && (
        <div className="mt-4">
          <Button variant="ghost" size="sm" onClick={() => setShowWording((v) => !v)}>
            {showWording ? "Hide delivered wording" : `Show delivered wording (${d.wording.length})`}
          </Button>
          {showWording && (
            <ul className="mt-2 space-y-3">
              {d.wording.map((w: Json, n: number) => (
                <li key={n} className="rounded-xl border border-line p-3 text-sm">
                  <div className="text-[12px] text-ink-subtle">
                    v{w.version} · {channelName(w.channel)} · sent {w.times} time{w.times === 1 ? "" : "s"}
                  </div>
                  {w.subject && <div className="font-semibold">{w.subject}</div>}
                  <p className="whitespace-pre-line text-ink-muted">{w.body}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ page */

export default function ContentDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const toast = useToast();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const detail = useQuery({ queryKey: ["content", id], queryFn: () => api(`/content/${id}`) });
  useEffect(() => {
    // Opening the page marks decisions and messages seen: refresh the menu counts.
    if (detail.data) void client.invalidateQueries({ queryKey: ["attention"] });
  }, [detail.data, client]);
  const refresh = (data: Json, message?: string) => {
    client.setQueryData(["content", data.content_id], data);
    void client.invalidateQueries({ queryKey: ["content"] });
    void client.invalidateQueries({ queryKey: ["attention"] });
    if (message) toast(message);
  };
  const save = useMutation({
    mutationFn: (body: Json) => patch(`/content/${id}`, body),
    onSuccess: (d: Json) => {
      setEditing(false);
      refresh(d, "Draft saved.");
    },
  });
  const submit = useMutation({
    mutationFn: () => post(`/content/${id}/submit`),
    onSuccess: (d: Json) => refresh(d, `Version ${d.version} submitted for MLR review.`),
  });
  const revise = useMutation({
    mutationFn: () => post(`/content/${id}/revise`),
    onSuccess: (d: Json) => {
      refresh(d, `Version ${d.version} opened as a draft.`);
      navigate(`/content/${d.content_id}`);
    },
  });

  if (detail.isLoading) return <Loading label="Loading content" />;
  if (detail.error) return <ErrorState error={detail.error} retry={() => void detail.refetch()} variant="page" title="Content could not be loaded" />;
  const c = detail.data!;
  const reviewer = can(P.CONTENT_APPROVE);
  const decisionDone = (d: Json) => {
    const last = d.reviews?.[d.reviews.length - 1];
    refresh(d, last ? `${DECISION[last.decision]?.label ?? "Decision recorded"} for ${d.content_id} v${d.version}.` : "Review started.");
  };

  return (
    <>
      <PageHeader
        back
        title={c.title}
        subtitle={`${c.content_id} · version ${c.version}${c.previous_id ? ` (revises ${c.previous_id})` : ""}${c.author ? ` · proposed by ${c.mine ? "you" : c.author}` : c.origin === "library" ? " · library material" : ""}`}
        meta={
          <>
            <Morph value={c.mlr_status}>
              <MlrBadge status={c.mlr_status} expired={c.mlr_status === "approved" && c.is_expired} />
            </Morph>
            <Badge tone="sage">{c.audience === "HCP" ? "HCP audience" : "Patient audience"}</Badge>
          </>
        }
      />

      {c.mine && <ContentLifecycle c={c} />}
      {c.withdrawn_reason && c.mlr_status === "withdrawn" && (
        <Alert tone="bad" className="mb-4" title="Withdrawn">
          {c.withdrawn_reason}
        </Alert>
      )}
      {c.actions.open_version && c.actions.open_version !== c.content_id && (
        <Alert tone="info" className="mb-4" title="A newer version is open">
          <Link className="font-semibold text-primary-ink underline" to={`/content/${c.actions.open_version}`}>
            Go to {c.actions.open_version}
          </Link>
        </Alert>
      )}
      {(c.actions.edit || c.actions.submit || c.actions.revise) && (
        <Card className="mb-4">
          <ErrorNote error={save.error ?? submit.error ?? revise.error} className="mb-3" />
          {c.missing_for_review?.length > 0 && c.mlr_status === "draft" && (
            <Alert tone="warn" className="mb-3" title="Before MLR can review it">
              Add {c.missing_for_review.join(", ")}.
            </Alert>
          )}
          <div className="flex flex-wrap gap-2">
            {c.actions.edit && !editing && (
              <Button variant="secondary" onClick={() => setEditing(true)}>
                <FilePen className="h-4 w-4" aria-hidden /> Edit draft
              </Button>
            )}
            {c.actions.submit && (
              <Button variant="primary" busy={submit.isPending} disabled={editing} onClick={() => submit.mutate()}>
                <Send className="h-4 w-4" aria-hidden /> Submit for MLR review
              </Button>
            )}
            {c.actions.revise && (
              <Button variant={c.mlr_status === "approved" ? "secondary" : "primary"} busy={revise.isPending} onClick={() => revise.mutate()}>
                <GitBranch className="h-4 w-4" aria-hidden /> Open version {Math.max(...c.versions.map((v: Json) => v.version)) + 1}
              </Button>
            )}
          </div>
          {c.mlr_status === "approved" && c.actions.revise && (
            <p className="mt-2 text-[13px] text-ink-subtle">Version {c.version} stays in use until MLR approves the new one, which then replaces it.</p>
          )}
          {editing && (
            <div className="mt-4">
              <ContentForm initial={c} library={c.origin === "library"} saving={save.isPending} onSave={(b) => save.mutate(b)} onCancel={() => setEditing(false)} />
            </div>
          )}
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="space-y-4 lg:col-span-7">
          <Card title="What the recipient reads" description="Delivered exactly as written; never edited at send.">
            <p className="whitespace-pre-line text-sm leading-6 text-ink">{c.body}</p>
            <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[13px] text-ink-subtle">
              {c.channels.map((ch: string) => (
                <span key={ch} className="inline-flex items-center gap-1">
                  <ChannelIcon channel={ch} className="h-3.5 w-3.5" /> {channelName(ch)}
                </span>
              ))}
              <span>{c.specialty ? `For ${c.specialty}` : "Any specialty"}</span>
              <span>{c.jurisdiction_names?.length ? `Only in ${c.jurisdiction_names.join(", ")}` : "No country restriction"}</span>
              <span className="tabular">
                {c.effective_date ? `Effective ${fmtDate(c.effective_date)} · ${c.is_expired ? "expired" : "valid until"} ${fmtDate(c.expiry_date)}` : "Not approved"}
              </span>
            </div>
          </Card>
          <Card title="For MLR review">
            <dl className="space-y-4 text-sm">
              <div>
                <dt className="font-semibold text-ink">Claims and supporting references</dt>
                <dd>
                  {c.claims.length ? (
                    <ol className="mt-1 list-decimal space-y-1 pl-5">
                      {c.claims.map((x: Json, n: number) => (
                        <li key={n}>
                          {x.text}
                          <span className={cx("block text-[13px]", x.reference ? "text-ink-subtle" : "text-bad")}>
                            {x.reference ? `Reference: ${x.reference}` : "No reference"}
                          </span>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <span className="text-bad">No claims recorded</span>
                  )}
                </dd>
              </div>
              <div>
                <dt className="font-semibold text-ink">Product or medicine</dt>
                <dd className="text-ink-muted">{c.product ?? "General material (no product named)"}</dd>
              </div>
              <div>
                <dt className="font-semibold text-ink">Indication</dt>
                <dd className="text-ink-muted">{c.indication ?? <span className="text-bad">Not given</span>}</dd>
              </div>
              <div>
                <dt className="font-semibold text-ink">Safety and benefit-risk</dt>
                <dd className="text-ink-muted">{c.safety_info ?? <span className="text-bad">Not given</span>}</dd>
              </div>
              <div>
                <dt className="font-semibold text-ink">Labelling</dt>
                <dd className="text-ink-muted">{c.labelling_note ?? "Not given"}</dd>
              </div>
            </dl>
          </Card>
          {c.changes.length > 0 && (
            <Card title={`Changes since ${c.previous_id}`}>
              <ul className="space-y-3 text-sm">
                {c.changes.map((ch: Json) => (
                  <li key={ch.field}>
                    <div className="font-semibold text-ink">{FIELD_LABEL[ch.field] ?? ch.field}</div>
                    <div className="mt-1 grid gap-2 sm:grid-cols-2">
                      <p className="rounded-lg bg-bad-soft/40 p-2 text-ink-muted line-through decoration-bad/40">{show(ch.before)}</p>
                      <p className="rounded-lg bg-ok-soft/50 p-2 text-ink">{show(ch.after)}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <History c={c} onPosted={(d) => refresh(d, "Message sent.")} />
        </div>
        <div className="space-y-4 lg:col-span-5">
          {reviewer && <DecisionPanel key={`${c.content_id}-${c.mlr_status}`} c={c} onDone={decisionDone} />}
          <Card title="Version history" description="Each version of this material and the MLR decisions on it, oldest first.">
            <VersionTimeline versions={c.versions} reviews={c.reviews} current={c.content_id} />
          </Card>
          {reviewer && c.recommendations_waiting > 0 && (
            <Alert tone="warn" title="Recommendations waiting">
              {c.recommendations_waiting} recommendation{c.recommendations_waiting === 1 ? " is" : "s are"} held back by this item. Approval takes effect at the next engine cycle.
            </Alert>
          )}
        </div>
      </div>
      {c.deliveries && (
        <div className="mt-4">
          <Deliveries d={c.deliveries} />
        </div>
      )}
      {!c.deliveries && !reviewer && c.mlr_status === "approved" && !c.mine && (
        <EmptyState title="Approved for use" compact>
          The engine recommends it to your HCPs when it fits; you choose the variant and send.
        </EmptyState>
      )}
    </>
  );
}
