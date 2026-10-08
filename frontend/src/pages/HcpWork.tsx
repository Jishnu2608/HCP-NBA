import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, CalendarPlus, CheckCircle2, ClipboardList, Hand, Send, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, patch, post, query } from "../api";
import type { Json } from "../api";
import { BarList, CalendarStrip, ChartPanel, ChartTable, StackedBar, StreakCard, WindowChart, useInsights } from "../charts";
import { BentoGrid, SectionHeader } from "../layout";
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
  Loading,
  PageHeader,
  Segmented,
  TextArea,
  TextField,
  Toolbar,
  channelName,
  cx,
  fmtDate,
  fmtDateTime,
} from "../ui";

const KIND: Record<string, string> = { hcp_request: "HCP request", follow_up: "Follow-up", meeting: "Meeting" };
const MODE: Record<string, string> = { in_person: "In person", phone: "Phone call", video: "Video call" };
const OUTCOME_REASON: Record<string, string> = {
  interested: "Interested",
  need_info: "Needs more information",
  another_meeting: "Wants another meeting",
  follow_up: "Follow-up required",
  not_now: "Not now",
};
const LOCAL_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const select = "h-11 w-full rounded-lg border border-line bg-surface px-3 text-sm";

function useRefresh() {
  const client = useQueryClient();
  return () => {
    for (const key of ["hcp-work", "attention", "hcp", "nba"]) void client.invalidateQueries({ queryKey: [key] });
  };
}

/** When a meeting happens, in the representative's own clock, with the meeting's zone. */
function when(t: Json) {
  if (t.kind === "meeting" && t.scheduled_at) {
    const local = fmtDateTime(t.scheduled_at);
    return `${local}${t.timezone && t.timezone !== LOCAL_TZ ? ` (meeting time zone ${t.timezone})` : ""}`;
  }
  return t.due_date ? `Due ${fmtDate(t.due_date)}` : "";
}

/* ------------------------------------------------------------------ forms */

export function TaskForm({
  hcpId,
  source,
  defaultKind = "follow_up",
  contentId,
  onDone,
  onCancel,
}: {
  hcpId: string;
  source?: Json;
  defaultKind?: "follow_up" | "meeting";
  contentId?: string | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const toast = useToast();
  const refresh = useRefresh();
  const [kind, setKind] = useState<"follow_up" | "meeting">(defaultKind);
  const [reason, setReason] = useState(source ? `${source.intent_label ?? KIND[source.kind]}: ${source.content?.title ?? ""}`.trim() : "");
  const [due, setDue] = useState("");
  const [at, setAt] = useState("");
  const [tz, setTz] = useState(LOCAL_TZ);
  const [mode, setMode] = useState("in_person");
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: () =>
      post("/hcp-work", {
        hcp_id: hcpId,
        kind,
        reason,
        note: note || null,
        content_id: contentId ?? source?.content?.content_id ?? null,
        due_date: kind === "follow_up" ? due : null,
        scheduled_at: kind === "meeting" ? at : null,
        timezone: kind === "meeting" ? tz : null,
        mode: kind === "meeting" ? mode : null,
        source_task_id: source?.id ?? null,
      }),
    onSuccess: () => {
      toast(kind === "meeting" ? "Meeting scheduled." : "Follow-up added.");
      refresh();
      onDone();
    },
  });
  return (
    <div className="space-y-3 rounded-xl border border-line p-4">
      <ErrorNote error={save.error} />
      <Segmented
        label="Kind"
        value={kind}
        onChange={setKind}
        options={[
          { value: "follow_up", label: "Follow-up" },
          { value: "meeting", label: "Meeting or call" },
        ]}
      />
      {source?.requested_by_hcp && kind === "meeting" && (
        <p className="text-[13px] text-ink-subtle">The HCP asked for this meeting, so the contact gap does not apply.</p>
      )}
      <TextField label="Purpose" value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
      {kind === "follow_up" ? (
        <TextField label="Due date" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-3">
          <TextField label="Date and time" type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
          <label className="text-sm font-medium text-ink">
            Time zone
            <input className={cx(select, "mt-1.5")} value={tz} onChange={(e) => setTz(e.target.value)} aria-label="Time zone" />
          </label>
          <label className="text-sm font-medium text-ink">
            How
            <select className={cx(select, "mt-1.5")} value={mode} onChange={(e) => setMode(e.target.value)}>
              {Object.entries(MODE).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      <TextArea label="Note (optional)" rows={2} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
      <div className="flex gap-2">
        <Button
          variant="primary"
          busy={save.isPending}
          disabled={!reason.trim() || (kind === "follow_up" ? !due : !at)}
          onClick={() => save.mutate()}
        >
          <CalendarPlus className="h-4 w-4" aria-hidden /> {kind === "meeting" ? "Schedule" : "Add follow-up"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function CompleteForm({ task, onDone, onCancel }: { task: Json; onDone: () => void; onCancel: () => void }) {
  const toast = useToast();
  const refresh = useRefresh();
  const [outcome, setOutcome] = useState(task.kind === "hcp_request" ? "answered" : "completed");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [next, setNext] = useState(false);
  const done = useMutation({
    mutationFn: () => patch(`/hcp-work/${task.id}`, { action: "complete", outcome, outcome_reason: reason || null, note: note || null }),
    onSuccess: () => {
      toast("Recorded.");
      // With something learned, offer the next step first; refreshing now would remove this
      // row (and the offer) from a view that lists open work only.
      if (reason) setNext(true);
      else {
        refresh();
        onDone();
      }
    },
  });
  if (next)
    return (
      <div className="space-y-2">
        <p className="text-sm text-ink">Add the next step for this HCP?</p>
        <TaskForm
          hcpId={task.hcp_id}
          contentId={task.content?.content_id}
          onDone={onDone}
          onCancel={() => {
            refresh();
            onDone();
          }}
        />
      </div>
    );
  return (
    <div className="space-y-3 rounded-xl border border-line p-4">
      <ErrorNote error={done.error} />
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm font-medium text-ink">
          Outcome
          <select className={cx(select, "mt-1.5")} value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            {task.kind === "hcp_request" && <option value="answered">Answered</option>}
            <option value="completed">Completed</option>
            <option value="no_response">No response</option>
            <option value="declined">Declined</option>
          </select>
        </label>
        <label className="text-sm font-medium text-ink">
          What you learned
          <select className={cx(select, "mt-1.5")} value={reason} onChange={(e) => setReason(e.target.value)}>
            <option value="">Nothing further</option>
            {Object.entries(OUTCOME_REASON).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
      </div>
      <TextArea label="Note (optional)" rows={2} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
      <div className="flex gap-2">
        <Button variant="primary" busy={done.isPending} onClick={() => done.mutate()}>
          <CheckCircle2 className="h-4 w-4" aria-hidden /> Record
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Back
        </Button>
      </div>
    </div>
  );
}

function RescheduleForm({ task, onDone, onCancel }: { task: Json; onDone: () => void; onCancel: () => void }) {
  const refresh = useRefresh();
  const [value, setValue] = useState("");
  const [cancelNote, setCancelNote] = useState("");
  const move = useMutation({
    mutationFn: () =>
      patch(`/hcp-work/${task.id}`, task.kind === "meeting" ? { action: "reschedule", scheduled_at: value, timezone: task.timezone } : { action: "reschedule", due_date: value }),
    onSuccess: () => {
      refresh();
      onDone();
    },
  });
  const cancel = useMutation({
    mutationFn: () => patch(`/hcp-work/${task.id}`, { action: "cancel", note: cancelNote }),
    onSuccess: () => {
      refresh();
      onDone();
    },
  });
  return (
    <div className="space-y-3 rounded-xl border border-line p-4">
      <ErrorNote error={move.error ?? cancel.error} />
      {task.kind !== "hcp_request" && (
        <div className="flex flex-wrap items-end gap-2">
          <TextField
            label={task.kind === "meeting" ? "New date and time" : "New due date"}
            type={task.kind === "meeting" ? "datetime-local" : "date"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <Button busy={move.isPending} disabled={!value} onClick={() => move.mutate()}>
            <CalendarClock className="h-4 w-4" aria-hidden /> Reschedule
          </Button>
        </div>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <TextField label="Reason to cancel" value={cancelNote} maxLength={500} onChange={(e) => setCancelNote(e.target.value)} />
        <Button variant="quiet-danger" busy={cancel.isPending} disabled={!cancelNote.trim()} onClick={() => cancel.mutate()}>
          <XCircle className="h-4 w-4" aria-hidden /> Cancel it
        </Button>
      </div>
      <Button variant="ghost" onClick={onCancel}>
        Back
      </Button>
    </div>
  );
}

export function TaskRow({ t, showHcp = true }: { t: Json; showHcp?: boolean }) {
  const [mode, setMode] = useState<null | "answer" | "complete" | "move">(null);
  const open = t.status === "open" || t.status === "scheduled";
  return (
    <li className="py-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={t.kind === "hcp_request" ? "accent" : t.kind === "meeting" ? "info" : "neutral"}>{KIND[t.kind]}</Badge>
        {t.intent_label && <Badge tone="sage">{t.intent_label}</Badge>}
        {t.overdue && <Badge tone="bad">Overdue</Badge>}
        {t.due_today && !t.overdue && <Badge tone="warn">Today</Badge>}
        {!open && <Badge tone="ok">{t.status === "cancelled" ? "Cancelled" : "Done"}</Badge>}
        {showHcp && (
          <Link to={`/hcps/${t.hcp_id}`} className="text-sm font-semibold text-primary-ink hover:underline">
            {t.hcp_name}
          </Link>
        )}
      </div>
      <p className="mt-1 text-sm text-ink">{t.reason}</p>
      {t.note && <p className="text-[13px] text-ink-muted">“{t.note}”</p>}
      <p className="mt-1 text-[13px] text-ink-subtle">
        {when(t)}
        {t.mode ? ` · ${MODE[t.mode]}` : ""}
        {t.content ? ` · ${t.content.product ? `${t.content.product}: ` : ""}${t.content.title} (v${t.content.version})` : ""}
        {t.owner ? ` · owner ${t.owner}` : " · no representative assigned"}
        {t.from_hcp ? " · from the HCP" : ""}
        {t.outcome ? ` · ${t.outcome}${t.outcome_reason ? `, ${OUTCOME_REASON[t.outcome_reason] ?? t.outcome_reason}` : ""}` : ""}
      </p>
      {open && !mode && (
        <div className="mt-2 flex flex-wrap gap-2">
          {t.kind === "hcp_request" && (
            <Button size="sm" variant="primary" onClick={() => setMode("answer")}>
              <CalendarPlus className="h-4 w-4" aria-hidden /> {t.intent === "request_meeting" ? "Schedule the meeting" : "Plan the follow-up"}
            </Button>
          )}
          <Button size="sm" variant={t.kind === "hcp_request" ? "secondary" : "primary"} onClick={() => setMode("complete")}>
            <CheckCircle2 className="h-4 w-4" aria-hidden /> {t.kind === "meeting" ? "Log meeting" : t.kind === "hcp_request" ? "Mark answered" : "Complete"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setMode("move")}>
            {t.kind === "hcp_request" ? "Close" : "Reschedule or cancel"}
          </Button>
        </div>
      )}
      {mode === "answer" && (
        <div className="mt-3">
          <TaskForm hcpId={t.hcp_id} source={t} defaultKind={t.intent === "request_meeting" ? "meeting" : "follow_up"} onDone={() => setMode(null)} onCancel={() => setMode(null)} />
        </div>
      )}
      {mode === "complete" && (
        <div className="mt-3">
          <CompleteForm task={t} onDone={() => setMode(null)} onCancel={() => setMode(null)} />
        </div>
      )}
      {mode === "move" && (
        <div className="mt-3">
          <RescheduleForm task={t} onDone={() => setMode(null)} onCancel={() => setMode(null)} />
        </div>
      )}
    </li>
  );
}

/* ------------------------------------------------------------------ HCP 360 panel */

export function EngagePanel({ hcp }: { hcp: Json }) {
  const navigate = useNavigate();
  const toast = useToast();
  const refresh = useRefresh();
  const [adding, setAdding] = useState<null | "follow_up" | "meeting">(null);
  const options = useQuery({ queryKey: ["hcp", hcp.hcp_id, "options"], queryFn: () => api(`/hcps/${hcp.hcp_id}/contact-options`) });
  const propose = useMutation({
    mutationFn: (o: Json) => post(`/hcps/${hcp.hcp_id}/contact`, { content_id: o.content_id, channel: o.channel }),
    onSuccess: (nba: Json) => {
      toast("Contact proposed. Review the message and send it.");
      refresh();
      navigate(`/nba/${nba.id}`);
    },
  });
  const w = hcp.contact_window;
  const list: Json[] = options.data?.options ?? [];
  const usable = list.filter((o) => o.eligible);
  return (
    <Card
      title="Engage this HCP"
      description="Approved content only, through the same safeguards as the engine: MLR approval, specialty, country, channel and contact limits."
    >
      {w &&
        (w.allowed_now ? (
          <Alert tone="ok" className="mb-4" title="Contact allowed today">
            {w.last_contact ? `Last contact ${fmtDate(w.last_contact)}.` : "No contact on record."}
          </Alert>
        ) : (
          <Alert tone="warn" className="mb-4" title={`Next contact possible from ${fmtDate(w.next_allowed)}`}>
            {w.reason}. Meetings the HCP asks for are not limited.
          </Alert>
        ))}
      <ErrorNote error={propose.error} className="mb-3" />
      {options.isLoading ? (
        <p className="text-sm text-ink-subtle">Checking approved content…</p>
      ) : options.error ? (
        <ErrorNote error={options.error} />
      ) : !list.length ? (
        <p className="text-sm text-ink-subtle">No approved content suits this HCP's specialty, country and history now.</p>
      ) : (
        <ul className="divide-y divide-line">
          {list.slice(0, 12).map((o) => (
            <li key={`${o.content_id}-${o.channel}`} className="flex flex-wrap items-center justify-between gap-2 py-3">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-ink">
                  {o.product ? `${o.product}: ` : ""}
                  {o.title}
                </div>
                <div className="flex flex-wrap items-center gap-2 text-[13px] text-ink-subtle">
                  <span>
                    {o.content_id} v{o.version}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <ChannelIcon channel={o.channel} className="h-3.5 w-3.5" /> {channelName(o.channel)}
                  </span>
                  {!o.eligible && <span className="text-warn">{o.reason}</span>}
                </div>
              </div>
              <Button size="sm" variant={o.eligible ? "primary" : "ghost"} disabled={!o.eligible} busy={propose.isPending} onClick={() => propose.mutate(o)}>
                <Send className="h-4 w-4" aria-hidden /> Propose
              </Button>
            </li>
          ))}
        </ul>
      )}
      {usable.length === 0 && list.length > 0 && (
        <p className="mt-2 text-[13px] text-ink-subtle">Nothing can be sent today; a meeting or a follow-up can still be planned.</p>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => setAdding("meeting")}>
          <CalendarPlus className="h-4 w-4" aria-hidden /> Schedule meeting or call
        </Button>
        <Button variant="ghost" onClick={() => setAdding("follow_up")}>
          <Hand className="h-4 w-4" aria-hidden /> Add follow-up
        </Button>
      </div>
      {adding && (
        <div className="mt-3">
          <TaskForm hcpId={hcp.hcp_id} defaultKind={adding} onDone={() => setAdding(null)} onCancel={() => setAdding(null)} />
        </div>
      )}
      {hcp.open_work?.length > 0 && (
        <>
          <h3 className="mt-5 text-sm font-semibold text-ink">Open work with this HCP</h3>
          <ul className="divide-y divide-line">
            {hcp.open_work.map((t: Json) => (
              <TaskRow key={t.id} t={t} showHcp={false} />
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ insights */

const WINDOW_ROWS = 10;

/** The representative's figures over their assigned HCPs (GET /api/insights/rep): the next
 *  14 days, the on-time streak, when each HCP may next be contacted (the frequency rules,
 *  shown, never bypassed), where each HCP stands, and what the last 90 days produced. */
function RepInsights({ onView }: { onView: (v: View) => void }) {
  const navigate = useNavigate();
  const q = useInsights("rep");
  const [allWindows, setAllWindows] = useState(false);
  const d: Json | undefined = q.data;
  const windows: Json[] = d?.contact_windows.items ?? [];
  const shownWindows = allWindows ? windows : windows.slice(0, WINDOW_ROWS);
  const engagement: Json | undefined = d?.engagement;
  const activity: Json | undefined = d?.activity;
  return (
    <>
      <SectionHeader title="Coming up" description="Your HCP work over the next two weeks." />
      <BentoGrid>
        <ChartPanel
          span="wide"
          title="Next 14 days"
          question="Follow-ups, meetings and requests falling on each day."
          query={q}
          note={
            d &&
            (d.upcoming.overdue ? (
              <button type="button" onClick={() => onView("overdue")} className="font-semibold text-bad hover:underline">
                {d.upcoming.overdue} overdue item{d.upcoming.overdue === 1 ? "" : "s"}: open the Overdue view
              </button>
            ) : (
              "Nothing overdue."
            ))
          }
          table={
            d && (
              <ChartTable
                caption="HCP work per day"
                head={["Day", "Follow-ups", "Meetings", "Requests"]}
                rows={d.upcoming.days.map((x: Json) => [fmtDate(x.date), x.follow_ups, x.meetings, x.requests])}
              />
            )
          }
        >
          {d && (
            <CalendarStrip
              label="HCP work per day for the next 14 days"
              days={d.upcoming.days.map((x: Json) => ({
                date: x.date,
                parts: [
                  { label: `follow-up${x.follow_ups === 1 ? "" : "s"}`, value: x.follow_ups, tone: "warn" as const },
                  { label: `meeting${x.meetings === 1 ? "" : "s"}`, value: x.meetings, tone: "info" as const },
                  { label: `request${x.requests === 1 ? "" : "s"}`, value: x.requests, tone: "brand" as const },
                ],
              }))}
            />
          )}
        </ChartPanel>
        <div className="flex min-w-0 flex-col gap-4 md:col-span-2 xl:col-span-4">
          {d && (
            <StreakCard
              streak={d.streak}
              label="Follow-ups and meetings completed on time, in a row"
              empty="Your streak starts with your first completed follow-up or meeting."
            />
          )}
        </div>
      </BentoGrid>

      <SectionHeader title="Your HCPs" description="Contact limits and where each assigned HCP stands." />
      <BentoGrid>
        <ChartPanel
          span="half"
          title="When you may next contact each HCP"
          question="The contact limits decide this: hatched days are a cool-down, blue days are open. Select an HCP to open their profile."
          query={q}
          empty={d && !windows.length ? "No assigned HCP is open to engagement." : false}
          note={
            windows.length > WINDOW_ROWS ? (
              <button type="button" onClick={() => setAllWindows((v) => !v)} className="font-semibold text-primary-ink hover:underline">
                {allWindows ? "Show the first 10" : `Show all ${windows.length} HCPs`}
              </button>
            ) : undefined
          }
          table={
            <ChartTable
              caption="Next allowed contact per HCP"
              head={["HCP", "Last contact", "Next allowed"]}
              rows={windows.map((w) => [w.name, w.last_contact ? fmtDate(w.last_contact) : "None", w.allowed_now ? "Now" : fmtDate(w.next_allowed)])}
            />
          }
        >
          {d && (
            <WindowChart
              today={d.today}
              label="Days until each HCP may next be contacted"
              rows={shownWindows.map((w) => ({
                key: w.hcp_id,
                label: w.name,
                from: w.next_allowed,
                tip: {
                  title: w.name,
                  lines: [
                    w.allowed_now ? "Contact allowed now" : `Next contact allowed ${fmtDate(w.next_allowed)}`,
                    w.last_contact ? `Last contact ${fmtDate(w.last_contact)}` : "No contact on record",
                    `Minimum gap ${w.min_gap_days} days`,
                  ],
                },
                onOpen: () => navigate(`/hcps/${w.hcp_id}`),
              }))}
            />
          )}
        </ChartPanel>
        <ChartPanel
          span="half"
          title="Where your HCPs stand"
          question="Each assigned HCP once: open work first, then their latest signal in 90 days."
          query={q}
          empty={engagement && !engagement.total ? "No HCP is assigned to you." : false}
          table={
            engagement && (
              <ChartTable
                caption="Assigned HCPs by state"
                head={["State", "HCPs", "Who"]}
                rows={engagement.segments
                  .filter((s: Json) => s.value)
                  .map((s: Json) => [s.label, s.value, s.hcps.map((h: Json) => h.name).join(", ")])}
              />
            )
          }
        >
          {engagement && (
            <StackedBar
              unit="HCP"
              segments={engagement.segments.map((s: Json) => ({
                key: s.key,
                label: s.label,
                value: s.value,
                tone: s.tone,
                hint: s.hcps.length ? s.hcps.slice(0, 3).map((h: Json) => h.name).join(", ") + (s.hcps.length > 3 ? "…" : "") : undefined,
              }))}
            />
          )}
        </ChartPanel>
        <ChartPanel
          span="full"
          title="Your last 90 days"
          question="What your own work produced, as counts. These are separate counts, not a funnel: a meeting can come from an HCP's own request."
          query={q}
          note={
            activity &&
            (activity.response_rate != null
              ? `Responses recorded on ${Math.round(activity.response_rate * 100)}% of your sends.`
              : `A response rate appears from ${activity.min_rate_n} sends.`)
          }
          table={
            activity && <ChartTable caption="Your last 90 days" head={["Measure", "Count"]} rows={activity.stages.map((s: Json) => [s.label, s.value])} />
          }
        >
          {activity && (
            <BarList
              items={activity.stages.map((s: Json) => ({
                key: s.key,
                label: s.label,
                value: s.value,
                tone: "info",
                tip: { title: s.label, lines: [`${s.value} in the last ${activity.window_days} days`] },
              }))}
            />
          )}
        </ChartPanel>
      </BentoGrid>
    </>
  );
}

/* ------------------------------------------------------------------ page */

type View = "today" | "requests" | "follow_ups" | "meetings" | "overdue" | "done_today";

export default function HcpWork() {
  const [view, setView] = useState<View>("today");
  const work = useQuery({ queryKey: ["hcp-work", view], queryFn: () => api(`/hcp-work${query({ view })}`), refetchInterval: 60_000 });
  if (work.isLoading) return <Loading label="Loading your HCP work" />;
  if (work.error) return <ErrorState error={work.error} retry={() => void work.refetch()} variant="page" title="Your HCP work could not be loaded" />;
  const d = work.data!;
  const c = d.counts;
  return (
    <>
      <PageHeader
        title="My HCP work"
        subtitle="What your HCPs asked for, the follow-ups and meetings you owe them, and what you did today. Work for HCPs assigned to you, including what a previous representative left open."
      />
      <Toolbar>
        <Segmented
          label="View"
          value={view}
          onChange={setView}
          options={[
            { value: "today", label: "Due now", count: c.today },
            { value: "requests", label: "HCP requests", count: c.requests },
            { value: "follow_ups", label: "Follow-ups", count: c.follow_ups },
            { value: "meetings", label: "Meetings", count: c.meetings },
            { value: "overdue", label: "Overdue", count: c.overdue },
            { value: "done_today", label: "Done today", count: c.done_today },
          ]}
        />
      </Toolbar>
      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-7" flush>
          {!d.items.length ? (
            <EmptyState tone="ok" icon={<ClipboardList className="h-5 w-5" />} title={view === "done_today" ? "Nothing closed yet today" : "Nothing here"}>
              {view === "today" ? "No request, follow-up or meeting is due. New HCP requests appear here and in the menu count." : undefined}
            </EmptyState>
          ) : (
            <ul className="divide-y divide-line px-5 sm:px-6">
              {d.items.map((t: Json) => (
                <TaskRow key={t.id} t={t} />
              ))}
            </ul>
          )}
        </Card>
        <Card className="lg:col-span-5" title="Your engagements today" description="Messages sent, visits, calls and meetings you logged today.">
          {!d.engagements_today.length ? (
            <p className="text-sm text-ink-subtle">None yet today.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {d.engagements_today.map((e: Json) => (
                <li key={e.id} className="flex flex-wrap items-center gap-2">
                  <ChannelIcon channel={e.channel} className="h-3.5 w-3.5" />
                  <Link to={`/hcps/${e.hcp_id}`} className="font-semibold text-primary-ink hover:underline">
                    {e.hcp_name}
                  </Link>
                  <span className="text-ink-muted">
                    {e.content_title ?? e.type_label} · {e.outcome}
                    {e.outcome_reason ? ` (${OUTCOME_REASON[e.outcome_reason] ?? e.outcome_reason})` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-4 text-[13px] text-ink-subtle">
            {d.recommendations_open ? (
              <>
                Recommendations are still waiting in your <Link to="/queue" className="font-semibold text-primary-ink underline">HCP queue</Link>.
              </>
            ) : (
              "No recommendation is waiting in your HCP queue."
            )}
          </p>
        </Card>
      </div>
      <RepInsights onView={setView} />
    </>
  );
}
