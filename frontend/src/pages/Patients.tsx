import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  CalendarX2,
  Check,
  Gauge,
  HeartPulse,
  History,
  MessageCircleMore,
  MessageSquare,
  Pill,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Users,
  X,
} from "lucide-react";
import { useState } from "react";
import { ChartFrame, ChartPanel, ChartTable, Scatter, Sparkline, ToneKey, shortDate, useInsights } from "../charts";
import type { ChartTone } from "../charts";
import { BentoCard, BentoCell, BentoGrid } from "../layout";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, query } from "../api";
import type { Json } from "../api";
import {
  Avatar,
  Badge,
  Button,
  Card,
  ChannelIcon,
  DataTable,
  EmptyState,
  ErrorState,
  Loading,
  LoadingRows,
  Meter,
  PageHeader,
  RISK,
  SearchInput,
  Segmented,
  SegmentBadge,
  KpiGrid,
  Stat,
  StatusBadge,
  Toolbar,
  channelName,
  cx,
  fmtDate,
  fmtDateTime,
  num,
  pct,
  titleCase,
} from "../ui";
import type { Column } from "../ui";
import { useAuth } from "../auth";
import { P } from "../permissions";
import { CarePanel, HealthSummary, OriginBadge } from "./Care";
import { currencyFor, specialtyText } from "./HealthForms";

const DAY = 86_400_000;
const WINDOW_DAYS = 365;

type RiskFilter = "" | "high" | "medium" | "low";

const SEGMENT_TONE: Record<string, ChartTone> = { high: "bad", medium: "warn", low: "ok" };
const X_MAX_DAYS = 180;

/** Who on the care manager's panel may need attention: adherence risk against days since
 *  the last contact (GET /api/insights/care). High risk and long silence sit top right. */
function AttentionScatter() {
  const navigate = useNavigate();
  const q = useInsights("care");
  const a: Json | undefined = q.data?.attention;
  const points: Json[] = a?.points ?? [];
  const cut: Json = a?.cutoffs ?? {};
  return (
    <BentoGrid className="mb-6">
      <ChartPanel
        title="Who may need attention"
        question="Each point is a patient on your panel: adherence risk against days since the last contact. Select a point to open the patient."
        query={q}
        empty={a && !points.length ? "No patient on your panel has both a risk score and a recorded contact yet." : false}
        note={
          a &&
          [
            `${num(points.length)} of ${num(a.panel_size)} patients shown.`,
            a.no_contact_on_record ? `${num(a.no_contact_on_record)} with no contact on record are not placed.` : null,
            points.some((p) => p.days_since_contact > X_MAX_DAYS) ? `Contacts older than ${X_MAX_DAYS} days sit on the right edge.` : null,
          ]
            .filter(Boolean)
            .join(" ")
        }
        table={
          <ChartTable
            caption="Patients by risk and days since last contact"
            head={["Patient", "Risk score", "Days since contact"]}
            rows={points.map((p) => [
              <Link key={p.patient_id} to={`/patients/${p.patient_id}`} className="font-semibold text-primary-ink hover:underline">
                {p.name}
              </Link>,
              p.risk_score,
              p.days_since_contact,
            ])}
          />
        }
      >
        <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
          {(["high", "medium", "low"] as const).map((r) => (
            <span key={r} className="inline-flex items-center gap-1.5">
              <ToneKey tone={SEGMENT_TONE[r]} /> {RISK[r].label}
            </span>
          ))}
        </div>
        <Scatter
          label="Patients by adherence risk and days since last contact"
          xLabel="Days since the last contact"
          yLabel="Risk score"
          xMax={X_MAX_DAYS}
          yMax={100}
          xGuides={[{ value: 30, label: "30 days" }]}
          yGuides={[
            ...(cut.high != null ? [{ value: cut.high, label: `High risk from ${cut.high}` }] : []),
            ...(cut.medium != null ? [{ value: cut.medium, label: `Medium from ${cut.medium}` }] : []),
          ]}
          points={points.map((p) => ({
            key: p.patient_id,
            x: p.days_since_contact,
            y: p.risk_score,
            tone: SEGMENT_TONE[p.risk_segment] ?? "neutral",
            tip: {
              title: p.name,
              lines: [
                `Risk score ${p.risk_score}${p.risk_segment ? ` (${p.risk_segment})` : ""}`,
                `Last contact ${p.days_since_contact} days ago (${shortDate(p.last_contact)})`,
              ],
            },
            onOpen: () => navigate(`/patients/${p.patient_id}`),
          }))}
        />
      </ChartPanel>
    </BentoGrid>
  );
}

/** The patient's adherence risk score on each cycle date, once there are three. */
function RiskTrend({ history }: { history: Json[] }) {
  if ((history ?? []).length < 3) return null;
  const last = history[history.length - 1];
  return (
    <section
      aria-label="Adherence risk over time"
      className="mb-6 flex flex-wrap items-center gap-x-6 gap-y-3 rounded-xl border border-line bg-surface px-5 py-4 shadow-card sm:px-6"
    >
      <div className="min-w-0">
        <p className="text-[13px] text-ink-subtle">Adherence risk score over time</p>
        <p className="tabular text-lg font-semibold text-ink">
          {last.risk_score} <span className="text-sm font-normal text-ink-muted">of 100 on {shortDate(last.date)}</span>
        </p>
      </div>
      <ChartFrame className="w-full max-w-sm flex-1">
        <Sparkline
          label="Risk score on each cycle date"
          domain={[0, 100]}
          points={history.map((h) => ({
            key: h.date,
            value: h.risk_score,
            tip: { title: fmtDate(h.date), lines: [`Risk score ${h.risk_score} of 100`] },
          }))}
        />
      </ChartFrame>
      <p className="text-xs text-ink-subtle">One point per engine cycle, worst therapy that day.</p>
    </section>
  );
}

export function PatientList() {
  const navigate = useNavigate();
  const { can } = useAuth();
  const [risk, setRisk] = useState<RiskFilter>("");
  const [q, setQ] = useState("");
  const list = useQuery({
    queryKey: ["patients", risk, q],
    queryFn: () => api(`/patients${query({ risk, q, limit: 100 })}`),
    placeholderData: (previous) => previous,
  });
  const byRisk: Record<string, number> = list.data?.by_risk ?? {};
  const total = Object.values(byRisk).reduce((a, b) => a + b, 0);
  const shown = (n: number) => (list.data ? num(n) : "—");

  const columns: Column<Json>[] = [
    {
      key: "patient",
      header: "Patient",
      primary: true,
      className: "min-w-56",
      cell: (p) => (
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={p.name} size="sm" />
          <div className="min-w-0">
            <Link
              to={`/patients/${p.patient_id}`}
              tabIndex={-1}
              onClick={(e) => e.stopPropagation()}
              className="block font-semibold text-ink [overflow-wrap:anywhere] hover:text-primary-ink hover:underline"
              title={p.name}
            >
              {p.name}
            </Link>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="tabular text-xs text-ink-subtle">{p.patient_id}</span>
              <OriginBadge origin={p.origin} />
            </div>
          </div>
        </div>
      ),
    },
    { key: "risk", header: "Adherence risk", hideOnMobile: true, cell: (p) => <SegmentBadge value={p.risk_segment} /> },
    { key: "plan", header: "Plan", cell: (p) => <span className="text-ink-muted">{p.plan_type ?? "—"}</span> },
    {
      // The list is ordered by this on the server: latest activity first.
      key: "activity",
      header: "Last activity",
      hideOnMobile: true,
      cell: (p) => <span className="tabular text-ink-muted">{p.last_activity_at ? fmtDate(p.last_activity_at) : "—"}</span>,
    },
    {
      key: "location",
      header: "Location",
      // Exactly what is on record: a demo record's city, or the country a real patient chose.
      cell: (p) => <span className="text-ink-muted">{p.location ?? "—"}</span>,
    },
    {
      key: "channel",
      header: "Preferred channel",
      cell: (p) => (
        <span className="inline-flex items-center gap-1.5 text-ink-muted">
          <ChannelIcon channel={p.preferred_channel} className="h-3.5 w-3.5" />
          {channelName(p.preferred_channel)}
        </span>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Patients"
        subtitle="Adherence risk is recalculated every cycle from days covered, the current gap and the refill trend."
      />
      <KpiGrid>
        <Stat label="Patients" value={shown(total)} icon={<Users className="h-4 w-4" aria-hidden />} />
        {(["high", "medium", "low"] as const).map((r) => (
          <Stat
            key={r}
            label={RISK[r].label}
            value={shown(byRisk[r] ?? 0)}
            hint={total ? `${pct((byRisk[r] ?? 0) / total)} of patients` : undefined}
            tone={r === "high" ? "bad" : r === "medium" ? "warn" : "ok"}
            icon={RISK[r].icon}
          />
        ))}
      </KpiGrid>
      {can(P.PATIENT_CARE_MANAGE) && <AttentionScatter />}
      <Toolbar>
        <Segmented
          label="Filter by risk"
          value={risk}
          onChange={setRisk}
          options={[
            { value: "", label: "All", count: total },
            { value: "high", label: "High risk", count: byRisk.high ?? 0 },
            { value: "medium", label: "Medium risk", count: byRisk.medium ?? 0 },
            { value: "low", label: "Low risk", count: byRisk.low ?? 0 },
          ]}
        />
        <SearchInput
          label="Search patients"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name or ID"
          className="w-full lg:w-72"
        />
      </Toolbar>
      <Card flush>
        {list.isLoading ? (
          <div className="p-5">
            <LoadingRows rows={8} label="Loading patients" />
          </div>
        ) : list.error ? (
          <ErrorState error={list.error} retry={() => void list.refetch()} title="Patients could not be loaded" />
        ) : !list.data.items.length ? (
          <EmptyState title={q ? "No patients match your search" : "No patients in this view"} icon={<Users className="h-5 w-5" />}>
            {q ? "Check the spelling, or search by patient ID." : risk === "high" ? "No high-risk patients are currently assigned." : undefined}
          </EmptyState>
        ) : (
          <>
            <DataTable
              caption="Patients"
              columns={columns}
              rows={list.data.items}
              rowKey={(p) => p.patient_id}
              onRowClick={(p) => navigate(`/patients/${p.patient_id}`)}
              mobileAside={(p) => <SegmentBadge value={p.risk_segment} />}
            />
            {list.data.total > list.data.items.length && (
              <p className="border-t border-line px-6 py-3 text-[13px] text-ink-subtle">
                Showing the first {list.data.items.length} of {num(list.data.total)}. Search to narrow the list.
              </p>
            )}
          </>
        )}
      </Card>
    </>
  );
}

/** Covered [start, end) intervals in ms. An early refill is carried forward, as in the engine. */
function coverage(fills: Json[]): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  let cursor = 0;
  for (const f of [...fills].sort((a, b) => a.fill_date.localeCompare(b.fill_date))) {
    const start = Math.max(Date.parse(f.fill_date), cursor);
    cursor = start + f.days_supply * DAY;
    out.push([start, cursor]);
  }
  return out;
}

const iso = (t: number) => new Date(t).toISOString().slice(0, 10);

export function CoverageTimeline({ fills, asOf }: { fills: Json[]; asOf: string }) {
  const end = Date.parse(asOf) + DAY;
  const start = end - WINDOW_DAYS * DAY;
  const x = (t: number) => ((Math.min(Math.max(t, start), end) - start) / (end - start)) * 100;
  const intervals = coverage(fills).filter(([, e]) => e > start);
  const months: number[] = [];
  const first = new Date(start);
  for (let d = new Date(first.getFullYear(), first.getMonth() + 1, 1); d.getTime() < end; d.setMonth(d.getMonth() + 1)) {
    months.push(d.getTime());
  }
  return (
    <figure aria-label="Medication supply over the last 12 months">
      <div
        className="relative h-8 overflow-hidden rounded-md ring-1 ring-inset ring-bad-line"
        style={{
          backgroundColor: "var(--bad-soft)",
          backgroundImage:
            "repeating-linear-gradient(135deg, transparent 0 5px, color-mix(in srgb, var(--bad) 14%, transparent) 5px 6px)",
        }}
      >
        {intervals.map(([s, e], i) => (
          <div
            key={i}
            title={`Covered ${fmtDate(iso(s))} to ${fmtDate(iso(e - DAY))}`}
            className="absolute inset-y-0 border-r-2 border-surface bg-primary"
            style={{ left: `${x(s)}%`, width: `${Math.max(x(e) - x(s), 0)}%` }}
          />
        ))}
        {fills
          .filter((f) => Date.parse(f.fill_date) >= start)
          .map((f, i) => (
            <div
              key={i}
              title={`Filled ${fmtDate(f.fill_date)} (${f.days_supply} days)`}
              className="absolute inset-y-0 w-0.5 bg-ink"
              style={{ left: `${x(Date.parse(f.fill_date))}%` }}
            />
          ))}
      </div>
      <div className="relative mt-1.5 h-4 text-[11px] text-ink-subtle" aria-hidden>
        {months.map((m, i) => (
          <span
            key={m}
            className={cx("absolute -translate-x-1/2", i % 2 === 1 && "hidden sm:inline")}
            style={{ left: `${x(m)}%` }}
          >
            {new Date(m).toLocaleDateString("en-US", { month: "short" })}
          </span>
        ))}
      </div>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-subtle">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-3 rounded-sm bg-primary" aria-hidden /> Medication on hand
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="h-2.5 w-3 rounded-sm ring-1 ring-bad-line"
            style={{
              backgroundColor: "var(--bad-soft)",
              backgroundImage:
                "repeating-linear-gradient(135deg, transparent 0 2px, color-mix(in srgb, var(--bad) 30%, transparent) 2px 3px)",
            }}
            aria-hidden
          />
          No supply
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-0.5 bg-ink" aria-hidden /> Refill
        </span>
      </figcaption>
    </figure>
  );
}

/** Response rate per channel as labelled bars. */
export function ChannelTable({ channels }: { channels: Record<string, Json> }) {
  const entries = Object.entries(channels);
  if (!entries.length) return <p className="text-sm text-ink-subtle">No outreach history yet.</p>;
  // Two columns once the card is wide enough; a channel never used is one quiet line, not
  // an empty bar.
  return (
    <div className="@container">
      <ul className="grid gap-x-8 gap-y-3.5 @lg:grid-cols-2">
        {entries.map(([name, c]) => (
          <li key={name}>
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className={cx("flex min-w-0 items-center gap-2", c.sent ? "text-ink" : "text-ink-muted")}>
                <ChannelIcon channel={name} className="h-4 w-4 shrink-0 text-ink-subtle" />
                <span className="truncate">{channelName(name)}</span>
              </span>
              {c.sent ? (
                <span className="tabular shrink-0 font-semibold text-ink">{pct(c.rate)}</span>
              ) : (
                <span className="shrink-0 text-xs text-ink-subtle">No history</span>
              )}
            </div>
            {c.sent > 0 && (
              <>
                <Meter value={c.rate} className="mt-1.5" label={`${channelName(name)} response rate`} />
                <div className="tabular mt-1 text-xs text-ink-subtle">{`${c.engaged} of ${c.sent} responded`}</div>
              </>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-4 text-xs text-ink-subtle">Rates are smoothed toward the average, so one response is not over-read.</p>
    </div>
  );
}

const OUTCOME_TONE = (outcome: string) =>
  outcome === "filled"
    ? "ok"
    : ["no_response", "declined"].includes(outcome)
      ? "neutral"
      : outcome === "pending"
        ? "warn"
        : "info";

/** Outreach history as a timeline: newest first, the first few shown. */
const INTENT_LABEL: Record<string, string> = {
  interested: "interested",
  request_meeting: "asked for a meeting",
  need_info: "needs more information",
  need_evidence: "needs evidence",
  not_now: "not now",
  decline: "not interested",
};
const REASON_LABEL: Record<string, string> = {
  interested: "Interested",
  need_info: "Needs more information",
  another_meeting: "Wants another meeting",
  follow_up: "Follow-up required",
  not_now: "Not now",
};

export function HistoryList({ items }: { items: Json[] }) {
  const [all, setAll] = useState(false);
  if (!items.length) {
    return <EmptyState compact title="No outreach on record" icon={<MessageCircleMore className="h-5 w-5" />} />;
  }
  const shown = all ? items : items.slice(0, 8);
  return (
    <>
      <ol className="relative space-y-1 before:absolute before:inset-y-3 before:left-[15px] before:w-px before:bg-line">
        {shown.map((i) => (
          <li key={i.id} className="relative flex gap-3 rounded-lg py-2.5">
            <span
              className={cx(
                "relative z-[1] grid h-8 w-8 shrink-0 place-items-center rounded-full ring-1",
                i.source === "nba" ? "bg-primary-soft text-primary-ink ring-primary-line" : "bg-surface text-ink-subtle ring-line-strong",
              )}
            >
              <ChannelIcon channel={i.channel} className="h-3.5 w-3.5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-sm font-semibold text-ink">{titleCase(i.type_label)}</span>
                <span className="text-sm text-ink-muted">by {i.channel_label}</span>
                <Badge tone={OUTCOME_TONE(i.outcome)}>{titleCase(i.outcome)}</Badge>
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-2 text-[13px] text-ink-subtle">
                <span className="tabular">{fmtDate(i.ts)}</span>
                {i.content_title && (
                  <span className="truncate">
                    · {i.product ? `${i.product}: ` : ""}
                    {i.content_title}
                    {i.content_version ? ` (v${i.content_version})` : ""}
                  </span>
                )}
                <span>· {i.source === "nba" ? "Recommendation" : i.source === "rep" ? "Meeting or call" : "Earlier outreach"}</span>
                {i.sent_by && <span>· by {i.sent_by}</span>}
              </div>
              {(i.intent || i.outcome_reason || i.note) && (
                <div className="mt-0.5 text-[13px] text-ink">
                  {i.intent && <Badge tone="accent">HCP: {INTENT_LABEL[i.intent] ?? i.intent}</Badge>}{" "}
                  {i.outcome_reason && <Badge tone="sage">{REASON_LABEL[i.outcome_reason] ?? i.outcome_reason}</Badge>}{" "}
                  {i.note && <span className="text-ink-muted">“{i.note}”</span>}
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>
      {items.length > 8 && (
        <Button variant="ghost" size="sm" className="mt-2" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${items.length} interactions`}
        </Button>
      )}
    </>
  );
}

/**
 * The open recommendation for a person, the most prominent element of a 360 page. Same
 * reading order as the recommendation page: action, why, channel and timing, compliance,
 * then the primary action.
 */
export function OpenNba({ nba, lastCycle }: { nba: Json | null; lastCycle?: string | null }) {
  if (!nba) {
    return (
      // Centred, because the card stretches to the height of its neighbour in the bento row.
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-line-strong bg-surface p-6 text-center">
        <span aria-hidden className="grid h-10 w-10 place-items-center rounded-xl bg-subtle text-ink-subtle">
          <Sparkles className="h-5 w-5" />
        </span>
        <div className="mt-3 text-[15px] font-semibold text-ink">No open recommendation</div>
        <p className="mt-1 max-w-md text-sm text-ink-muted">
          Recommendations are prepared at each engine cycle
          {lastCycle ? ` (last run ${fmtDateTime(lastCycle)})` : ""}; changes since then are considered at the next one.
        </p>
      </div>
    );
  }
  const blocked = nba.status === "blocked";
  return (
    <Link
      to={`/nba/${nba.id}`}
      className={cx(
        "lift group block overflow-hidden rounded-xl border bg-surface shadow-card",
        blocked ? "border-bad-line" : "border-primary-line",
      )}
    >
      <div className={cx("h-1", blocked ? "bg-bad-fill" : "bg-primary")} aria-hidden />
      <div className="p-5">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2 text-[13px] font-semibold text-ink-subtle">
            <span className={cx("h-2 w-2 rounded-full", blocked ? "bg-bad-fill" : "bg-primary")} aria-hidden /> Next best action
          </span>
          <StatusBadge status={nba.status} />
        </div>
        <div className={cx("mt-2 text-xl font-semibold leading-tight tracking-[-0.01em]", blocked ? "text-ink-muted" : "text-ink")}>
          {titleCase(nba.action_label)}
        </div>
        <div className="mt-3 text-[13px] font-semibold text-ink-muted">Why this action?</div>
        <p className={cx("mt-0.5 text-sm leading-6", blocked ? "text-bad" : "text-ink")}>
          {blocked && <ShieldAlert className="mr-1 inline h-4 w-4 align-[-3px]" aria-hidden />}
          {nba.block_reason ?? nba.rationale_summary ?? nba.timing_note}
        </p>
        <dl className="mt-4 grid grid-cols-2 gap-2">
          <div className="min-w-0 rounded-lg bg-subtle px-3 py-2">
            <dt className="text-xs text-ink-subtle">Channel</dt>
            <dd className="mt-0.5 flex items-center gap-1.5 text-sm font-semibold text-ink">
              <ChannelIcon channel={nba.channel} className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{titleCase(nba.channel_label)}</span>
            </dd>
          </div>
          <div className="min-w-0 rounded-lg bg-subtle px-3 py-2">
            <dt className="text-xs text-ink-subtle">Compliance</dt>
            <dd className={cx("mt-0.5 flex items-center gap-1.5 text-sm font-semibold", blocked ? "text-bad" : "text-ok")}>
              {blocked ? <ShieldAlert className="h-3.5 w-3.5 shrink-0" aria-hidden /> : <ShieldCheck className="h-3.5 w-3.5 shrink-0" aria-hidden />}
              <span className="truncate">{blocked ? "Blocked" : "Passed"}</span>
            </dd>
          </div>
          {nba.timing_note && !blocked && (
            <div className="col-span-2 min-w-0 rounded-lg bg-subtle px-3 py-2">
              <dt className="text-xs text-ink-subtle">Timing</dt>
              <dd className="mt-0.5 text-sm font-semibold text-ink">{nba.timing_note}</dd>
            </div>
          )}
        </dl>
        <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-semibold text-primary-ink">
          {blocked ? "See why it was blocked" : "Review recommendation"}
          <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
        </span>
      </div>
    </Link>
  );
}

function TherapyCard({ t, fills, asOf, country }: { t: Json; fills: Json[]; asOf: string; country?: string | null }) {
  const below = t.pdc !== null && t.pdc < 0.8;
  return (
    <BentoCard
      span="full"
      icon={<Pill />}
      title={
        <span className="flex flex-wrap items-center gap-x-2">
          {titleCase(t.drug_name)}
          <span className="font-normal text-ink-subtle">· {titleCase(t.measure)}</span>
        </span>
      }
      description={`Started ${fmtDate(t.start_date)} · ${t.days_supply}-day supply per fill`}
      action={<SegmentBadge value={t.risk_segment} />}
    >
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-6">
        <div className="col-span-2 sm:col-span-1 lg:col-span-2">
          <dt className="flex items-baseline justify-between text-[13px] text-ink-subtle">
            Days covered (PDC)
            <span className="text-xs">target 80%</span>
          </dt>
          <dd className={cx("tabular mt-0.5 text-2xl font-semibold", below ? "text-bad" : "text-ok")}>{pct(t.pdc)}</dd>
          <dd className="relative mt-1.5">
            <Meter value={t.pdc} tone={below ? "bad" : "ok"} label="Proportion of days covered" />
            <span className="absolute -top-0.5 h-3 w-0.5 rounded bg-ink" style={{ left: "80%" }} aria-hidden />
          </dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">MPR</dt>
          <dd className="tabular mt-0.5 text-lg font-semibold text-ink">{pct(t.mpr)}</dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Gap days</dt>
          <dd className={cx("tabular mt-0.5 text-lg font-semibold", t.gap_days > 0 ? "text-bad" : "text-ink")}>
            {t.gap_days ?? "—"}
          </dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Last refill</dt>
          <dd className="tabular mt-0.5 text-sm font-semibold text-ink">{fmtDate(t.last_fill_date)}</dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Risk score</dt>
          <dd className="tabular mt-0.5 text-lg font-semibold text-ink">
            {t.risk_score?.toFixed(0) ?? "—"}
            <span className="text-sm font-normal text-ink-subtle"> / 100</span>
          </dd>
          <dd className="tabular text-xs text-ink-subtle">
            Copay {currencyFor(country) || "$"}
            {t.copay?.toFixed(0)}
          </dd>
        </div>
      </dl>
      <div className="mt-6">
        <CoverageTimeline fills={fills} asOf={asOf} />
      </div>
    </BentoCard>
  );
}

export function PatientProfile() {
  const { id } = useParams();
  const { can } = useAuth();
  const profile = useQuery({ queryKey: ["patient", id], queryFn: () => api(`/patients/${id}`) });
  if (profile.isLoading) return <Loading label="Loading patient" />;
  if (profile.error) return <ErrorState error={profile.error} retry={() => void profile.refetch()} variant="page" title="This patient could not be loaded" />;
  const p: Json = profile.data;
  const current = p.consents.filter((c: Json) => c.in_effect);
  const outreach = current.filter((c: Json) => c.channel);
  const granted = outreach.filter((c: Json) => c.granted).length;
  const pdcs = p.therapies.map((t: Json) => t.pdc).filter((v: number | null) => v !== null) as number[];
  const lowestPdc = pdcs.length ? Math.min(...pdcs) : null;
  const longestGap = Math.max(0, ...p.therapies.map((t: Json) => t.gap_days ?? 0));

  return (
    <>
      <PageHeader
        back
        title={
          <span className="flex items-center gap-3">
            <Avatar name={p.name} size="lg" />
            <span className="min-w-0 break-words">{p.name}</span>
          </span>
        }
        meta={
          <span className="flex flex-wrap items-center gap-2">
            <SegmentBadge value={p.risk_segment} />
            <OriginBadge origin={p.origin} />
          </span>
        }
        subtitle={
          <span className="tabular">
            {[p.patient_id, `${p.age} years`, p.plan_type, p.location].filter(Boolean).join(" · ")}
          </span>
        }
      />

      <KpiGrid>
        <Stat
          label="Lowest days covered"
          value={pct(lowestPdc)}
          hint="target 80% (PDC)"
          tone={lowestPdc !== null && lowestPdc < 0.8 ? "bad" : "ok"}
          icon={<Gauge className="h-4 w-4" aria-hidden />}
        />
        <Stat
          label="Current gap"
          value={`${longestGap} days`}
          hint={longestGap ? "without medication" : "supply on hand"}
          tone={longestGap ? "bad" : undefined}
          icon={<CalendarX2 className="h-4 w-4" aria-hidden />}
        />
        <Stat label="Therapies" value={p.therapies.length} hint={p.therapies.map((t: Json) => titleCase(t.measure)).join(", ")} icon={<Pill className="h-4 w-4" aria-hidden />} />
        <Stat
          label="Preferred channel"
          value={<span className="text-lg">{channelName(p.preferred_channel)}</span>}
          hint="as stated by the patient"
          icon={<ChannelIcon channel={p.preferred_channel} />}
        />
        <Stat
          label="Outreach consent"
          value={`${p.outreach_channels_granted ?? granted} of ${p.outreach_channels_total ?? outreach.length}`}
          hint="channels granted"
          tone={(p.outreach_channels_granted ?? granted) ? undefined : "warn"}
          icon={<ShieldCheck className="h-4 w-4" aria-hidden />}
        />
      </KpiGrid>
      <RiskTrend history={p.risk_history} />

      {/* A real patient's care: managed by their care manager, read-only for other staff. */}
      {p.origin !== "synthetic" &&
        (can(P.PATIENT_CARE_MANAGE) ? (
          <CarePanel patientId={p.patient_id} />
        ) : (
          p.health && <HealthSummary health={p.health} />
        ))}

      {/* Bento: recommendation beside the consent it depends on; one full-width row per
          therapy (the supply timeline needs the width); the two short supporting cards as a
          pair; the outreach timeline, which grows with every contact, full width below.
          Phones read top to bottom in the same order. */}
      <BentoGrid>
        <BentoCell span="wide">
          <OpenNba nba={p.open_nba} lastCycle={p.last_cycle_at} />
        </BentoCell>
        <BentoCard span="narrow" icon={<ShieldCheck />} title="Consent on record" description="Only the patient can change consent.">
          <ul className="divide-y divide-line">
            {current.map((c: Json, i: number) => (
              <li key={i} className="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
                <span className="flex min-w-0 items-start gap-2 text-sm leading-5 text-ink">
                  {c.channel ? (
                    <ChannelIcon channel={c.channel} className="mt-0.5 h-4 w-4 shrink-0 text-ink-subtle" />
                  ) : (
                    <Stethoscope className="mt-0.5 h-4 w-4 shrink-0 text-ink-subtle" aria-hidden />
                  )}
                  <span className="min-w-0">{c.channel ? channelName(c.channel) : "Share adherence with provider"}</span>
                </span>
                {c.granted ? (
                  <Badge tone="ok" icon={<Check className="h-3.5 w-3.5" aria-hidden />} className="shrink-0">
                    Granted
                  </Badge>
                ) : (
                  <Badge tone="bad" icon={<X className="h-3.5 w-3.5" aria-hidden />} className="shrink-0">
                    Not granted
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        </BentoCard>

        {p.therapies.map((t: Json) => (
          <TherapyCard
            key={t.therapy_id}
            t={t}
            fills={p.fills.filter((f: Json) => f.therapy_id === t.therapy_id)}
            asOf={p.as_of_date}
            country={p.country}
          />
        ))}

        {p.features && (
          <BentoCard span="half" pairOnTablet icon={<MessageSquare />} title="Response by channel">
            <ChannelTable channels={p.features.channels} />
          </BentoCard>
        )}
        <BentoCard span={p.features ? "half" : "full"} pairOnTablet icon={<Users />} title="Care team">
          {p.care_team.length || (p.care_managers ?? []).length ? (
            <ul className="space-y-3">
              {(p.care_managers ?? []).map((m: Json) => (
                <li key={`cm-${m.id}`} className="flex items-center gap-3">
                  <Avatar name={m.name} size="sm" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold text-ink">{m.name}</div>
                    <div className="truncate text-[13px] text-ink-subtle">Care manager</div>
                  </div>
                </li>
              ))}
              {p.care_team.map((h: Json) => (
                <li key={h.hcp_id} className="flex items-center gap-3">
                  <Avatar name={h.name} size="sm" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold text-ink" title={h.name}>
                      {h.name}
                    </div>
                    <div className="truncate text-[13px] text-ink-subtle">{specialtyText(h.specialties)}</div>
                  </div>
                  {h.is_primary && (
                    <Badge tone="sage" icon={<HeartPulse className="h-3.5 w-3.5" aria-hidden />}>
                      Primary
                    </Badge>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ink-subtle">No care team on record.</p>
          )}
        </BentoCard>
        <BentoCard
          span="full"
          icon={<History />}
          title="Outreach timeline"
          description="Newest first. Engine recommendations are highlighted."
        >
          <HistoryList items={p.interactions} />
        </BentoCard>
      </BentoGrid>
    </>
  );
}
