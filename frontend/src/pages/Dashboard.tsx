import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Ban,
  BarChart3,
  CalendarX2,
  CheckCircle2,
  FileCheck2,
  HeartPulse,
  Lightbulb,
  ListChecks,
  MessageSquare,
  ScrollText,
  ShieldAlert,
  Stethoscope,
  TrendingUp,
} from "lucide-react";
import { useRef } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, query } from "../api";
import { BarList, ChartFrame, ChartPanel, ChartTable, Sparkline, shortDate, useInsights } from "../charts";
import { gsap, reducedMotion, useGSAP } from "../motion";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { BarRow, BentoCard, BentoGrid, InsightRow, SectionHeader } from "../layout";
import { P } from "../permissions";
import {
  AnimatedNumber,
  ErrorState,
  KpiCard,
  KpiGrid,
  Loading,
  LoadingRows,
  NBA_STATUS,
  PageHeader,
  Timeline,
  TimelineItem,
  cx,
  eventLabel,
  fmtDate,
  fmtDateTime,
  num,
  pct,
  titleCase,
} from "../ui";

// Chart colours come from CSS tokens so light and dark themes are defined once (index.css).
const BASELINE = "var(--series-baseline)";
const ENGINE = "var(--series-1)";
const MEASURE_COLOR: Record<string, string> = {
  diabetes: "var(--series-1)",
  hypertension: "var(--series-2)",
  cholesterol: "var(--series-3)",
};
const axis = { fontSize: 12, fill: "var(--chart-text)" };
const percent = (v: number) => `${Math.round(v * 100)}%`;
const tooltipProps = {
  contentStyle: {
    fontSize: 13,
    borderRadius: 10,
    border: "1px solid var(--line)",
    background: "var(--surface)",
    color: "var(--ink)",
    boxShadow: "var(--shadow-md)",
  },
  labelStyle: { color: "var(--ink)", fontWeight: 600 },
  itemStyle: { color: "var(--ink-muted)" },
  cursor: { fill: "var(--chart-cursor)" },
};
const legendProps = { iconType: "circle" as const, iconSize: 8, wrapperStyle: { fontSize: 12, color: "var(--chart-text)", paddingTop: 8 } };

// A rate from a handful of sends is noise: show the engine bar only with enough volume.
const MIN_SENT = 20;
const engineRate = (row: Json | undefined) => (row && row.sent >= MIN_SENT ? row.response_rate : undefined);
// Fixed chart height so charts in the same row line up.
const CHART_H = "h-64";

function count(rows: Json[], target: string, status: string) {
  return rows.filter((r) => r.target_type === target && r.status === status).reduce((n, r) => n + r.count, 0);
}

/** Order and colour of recommendation states in the distribution bars. */
const STATUS_ORDER: Array<{ key: string; fill: string }> = [
  { key: "ready_for_review", fill: "bg-info-fill" },
  { key: "approved", fill: "bg-primary" },
  { key: "sent", fill: "bg-sage-ink" },
  { key: "responded", fill: "bg-ok-fill" },
  { key: "blocked", fill: "bg-bad-fill" },
  { key: "rejected", fill: "bg-line-strong" },
  { key: "expired", fill: "bg-neutral-line" },
];

function StatusBar({ rows, target, label }: { rows: Json[]; target: string; label: string }) {
  const parts = STATUS_ORDER.map((s) => ({ ...s, n: count(rows, target, s.key) })).filter((s) => s.n > 0);
  const total = parts.reduce((a, b) => a + b.n, 0);
  return (
    <div>
      <div className="flex items-baseline justify-between text-sm">
        <span className="font-semibold text-ink">{label}</span>
        <span className="tabular text-ink-subtle">{num(total)} total</span>
      </div>
      <div className="mt-2 flex h-3 overflow-hidden rounded-full bg-sunken" role="img" aria-label={`${label}: ${parts.map((p) => `${NBA_STATUS[p.key]?.label ?? p.key} ${p.n}`).join(", ")}`}>
        {parts.map((p) => (
          <div key={p.key} className={cx("animate-grow h-full origin-left", p.fill)} style={{ width: `${(p.n / Math.max(total, 1)) * 100}%` }} />
        ))}
      </div>
      <ul className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-2">
        {parts.map((p) => (
          <li key={p.key} className="flex min-w-0 items-center gap-2">
            <span className={cx("h-2.5 w-2.5 shrink-0 rounded-sm", p.fill)} aria-hidden />
            <span className="truncate text-ink-muted">{NBA_STATUS[p.key]?.label ?? titleCase(p.key)}</span>
            <span className="tabular ml-auto font-semibold text-ink">{num(p.n)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-lg bg-subtle px-4 py-5 text-sm leading-6 text-ink-muted">{children}</p>;
}

/** One small line per series, each on its own scale from zero, with its total: never two
 *  measures on one axis. */
function SmallMultiples({ series, unit }: { series: Json[]; unit: string }) {
  const ref = useRef<HTMLUListElement>(null);
  const totals = useRef<Map<string, number> | null>(null);
  const signature = series.map((sr) => `${sr.key ?? sr.role}:${sr.points.reduce((n: number, p: Json) => n + p.value, 0)}`).join("|");
  // When a series' count rises between refreshes (not on first load), its latest point
  // pulses once so the change is noticed. Nothing repeats.
  useGSAP(
    () => {
      const now = new Map(series.map((sr) => [String(sr.key ?? sr.role), sr.points.reduce((n: number, p: Json) => n + p.value, 0)]));
      const before = totals.current;
      totals.current = now;
      if (!before || !ref.current || reducedMotion()) return;
      now.forEach((total, key) => {
        if (total > (before.get(key) ?? total)) {
          const dot = ref.current!.querySelector(`[data-series="${key}"] [data-last]`);
          if (dot) gsap.fromTo(dot, { scale: 2, transformOrigin: "50% 50%" }, { scale: 1, duration: 0.5, ease: "power2.out" });
        }
      });
    },
    { dependencies: [signature] },
  );
  return (
    <ul ref={ref} className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
      {series.map((sr) => {
        const top = Math.max(1, ...sr.points.map((p: Json) => p.value));
        return (
          <li key={sr.key ?? sr.role} data-series={sr.key ?? sr.role} className="min-w-0">
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 text-ink">{sr.label}</span>
              <span className="tabular shrink-0 font-semibold text-ink">{num(sr.total ?? sr.points.reduce((n: number, p: Json) => n + p.value, 0))}</span>
            </div>
            <ChartFrame className="mt-1">
              <Sparkline
                height={44}
                domain={[0, top]}
                label={`${sr.label} per day`}
                points={sr.points.map((p: Json) => ({
                  key: p.date,
                  value: p.value,
                  tip: { title: fmtDate(p.date), lines: [`${p.value} ${unit}`] },
                }))}
              />
            </ChartFrame>
          </li>
        );
      })}
    </ul>
  );
}

/** Platform health for administrators (GET /api/insights/operations): open work per staff
 *  account, failures per day and sign-ins per role. Counts only, no health information. */
function PlatformOperations() {
  const q = useInsights("operations");
  const d: Json | undefined = q.data;
  const workload: Json[] = d?.workload ?? [];
  const failures: Json[] = d?.failures ?? [];
  const roles: Json[] = d?.active_users.roles ?? [];
  const range = d ? `${shortDate(d.active_users.dates[0])} to ${shortDate(d.active_users.dates[d.active_users.dates.length - 1])}` : "";
  return (
    <>
      <SectionHeader title="Platform operations" description="Open work per account, failures and sign-ins. Counts only; no health information." />
      <BentoGrid>
        <ChartPanel
          span="half"
          title="Open work per account"
          question="Each staff account's open work, by the same definitions as its own menu counts. Select an account to manage it."
          query={q}
          link={{ to: "/users", label: "Users" }}
          empty={d && !workload.length ? "No staff account has a work queue." : false}
          table={
            <ChartTable
              caption="Open work per account"
              head={["Account", "Role", "Open work"]}
              rows={workload.map((w) => [w.name, w.role_label, w.open_work])}
            />
          }
        >
          <BarList
            items={workload.slice(0, 10).map((w) => ({
              key: String(w.user_id),
              label: w.name,
              sub: w.role_label,
              value: w.open_work,
              tone: "info",
              tip: { title: `${w.name} · ${w.role_label}`, lines: w.parts.map((p: Json) => `${p.label}: ${p.value}`) },
            }))}
          />
        </ChartPanel>
        <ChartPanel
          span="half"
          title="Sign-ins per day by role"
          question={`Accounts that signed in each day, ${range}.`}
          query={q}
          empty={d && !roles.length ? "No sign-in recorded in the last 14 days." : false}
          table={
            d && (
              <ChartTable
                caption="Sign-ins per day by role"
                head={["Day", ...roles.map((r) => r.label)]}
                rows={d.active_users.dates.map((day: string, i: number) => [fmtDate(day), ...roles.map((r) => r.points[i].value)])}
              />
            )
          }
        >
          <SmallMultiples series={roles} unit="accounts signed in" />
        </ChartPanel>
        <ChartPanel
          span="full"
          title="Failures and safeguard stops per day"
          question={`Each on its own scale, ${range}. A rise is worth a look; a single event is not a trend.`}
          query={q}
          table={
            d && (
              <ChartTable
                caption="Failures per day"
                head={["Day", ...failures.map((f) => f.label)]}
                rows={d.active_users.dates.map((day: string, i: number) => [fmtDate(day), ...failures.map((f) => f.points[i].value)])}
              />
            )
          }
        >
          <SmallMultiples series={failures} unit="events" />
        </ChartPanel>
      </BentoGrid>
    </>
  );
}

export default function Dashboard() {
  const { can } = useAuth();
  const overview = useQuery({ queryKey: ["analytics"], queryFn: () => api("/analytics/overview") });
  const recent = useQuery({
    queryKey: ["audit", "recent"],
    queryFn: () => api(`/audit${query({ limit: 8 })}`),
    enabled: can(P.AUDIT_READ),
  });
  const content = useQuery({
    queryKey: ["content"],
    queryFn: () => api<Json[]>("/content"),
    enabled: can(P.CONTENT_READ_ALL),
  });
  if (overview.isLoading) return <Loading label="Calculating metrics" />;
  if (overview.error) return <ErrorState error={overview.error} retry={() => void overview.refetch()} variant="page" />;

  const d: Json = overview.data;
  const status: Json[] = d.recommendations.by_status;
  const all = d.adherence.current.find((c: Json) => c.measure === "all");
  const risk = d.adherence.patients_by_risk;
  const queue = can(P.NBA_READ_ALL, P.NBA_READ_GATED) ? "/queue" : undefined;
  const patients = can(P.PATIENT_READ_ALL) ? "/patients" : undefined;

  const fair = Object.fromEntries(d.engagement.like_for_like.map((r: Json) => [r.source, r]));
  const fairRows = [
    { metric: "Responded", baseline: fair.baseline?.response_rate, engine: fair.engine?.response_rate },
    { metric: "Led to a refill", baseline: fair.baseline?.fill_rate, engine: fair.engine?.fill_rate },
  ];
  const hasEngine = (fair.engine?.sent ?? 0) >= MIN_SENT;

  const channelRows = (target: string) => {
    const rows = d.engagement.by_channel.filter((r: Json) => r.target_type === target && r.channel !== "all");
    const channels = [...new Set(rows.map((r: Json) => r.channel_label))] as string[];
    return channels.map((label) => ({
      channel: titleCase(label),
      baseline: rows.find((r: Json) => r.channel_label === label && r.source === "baseline")?.response_rate,
      engine: engineRate(rows.find((r: Json) => r.channel_label === label && r.source === "engine")),
    }));
  };
  const bestChannel = (target: string) =>
    channelRows(target)
      .filter((r) => r.baseline !== undefined)
      .sort((a, b) => (b.engine ?? b.baseline) - (a.engine ?? a.baseline))[0];

  const dates = [...new Set(d.adherence.trend.map((t: Json) => t.as_of_date))] as string[];
  const trendRows = dates.map((date) => {
    const row: Json = { date: fmtDate(date).replace(/, \d{4}$/, "") };
    for (const t of d.adherence.trend.filter((x: Json) => x.as_of_date === date)) row[t.measure] = t.adherent_rate;
    return row;
  });
  const measures = ["diabetes", "hypertension", "cholesterol"];
  const byMeasure: Json[] = d.adherence.current.filter((c: Json) => c.measure !== "all");
  const gateReasons: Json[] = d.recommendations.gate_reasons;
  const gateMax = Math.max(1, ...gateReasons.map((g) => g.count));
  const mix: Json[] = d.recommendations.mix;
  const blocked = count(status, "PATIENT", "blocked") + count(status, "HCP", "blocked");
  const totalRecs = status.reduce((n, r) => n + r.count, 0);
  const topGate = [...gateReasons].sort((a, b) => b.count - a.count)[0];
  const patientBest = bestChannel("PATIENT");
  const hcpBest = bestChannel("HCP");
  const mixFor = (target: string) => mix.filter((m) => m.target_type === target).sort((a, b) => b.count - a.count).slice(0, 5);
  const mixMax = Math.max(1, ...mix.map((m) => m.count));
  const items: Json[] = content.data ?? [];
  const pendingContent = items.filter((c) => c.mlr_status === "pending").length;
  const expiredContent = items.filter((c) => c.is_expired).length;

  return (
    <>
      <PageHeader title="Dashboard" subtitle="What the engine recommended, what the safeguards stopped, and what changed. Aggregates only." />

      {/* 1. Overview */}
      <h2 className="sr-only">Overview</h2>
      <KpiGrid>
        <KpiCard
          label="Patient actions ready"
          value={num(count(status, "PATIENT", "ready_for_review"))}
          hint="latest cycle"
          icon={<HeartPulse />}
          to={queue}
        />
        <KpiCard
          label="HCP actions ready"
          value={num(count(status, "HCP", "ready_for_review"))}
          hint="latest cycle"
          icon={<Stethoscope />}
          to={queue}
        />
        <KpiCard
          label="Blocked by safeguards"
          value={num(blocked)}
          hint="cannot be sent"
          tone={blocked ? "bad" : undefined}
          icon={<Ban />}
          to={queue}
        />
        <KpiCard
          label="Adherent therapies"
          value={pct(all?.adherent_rate, 1)}
          hint={`PDC ≥ ${pct(d.adherence.pdc_threshold)}`}
          tone="ok"
          icon={<CheckCircle2 />}
          to={patients}
        />
        <KpiCard
          label="Currently in a gap"
          value={pct(all?.in_gap_rate, 1)}
          hint="no supply now"
          tone="warn"
          icon={<CalendarX2 />}
          to={patients}
        />
        <KpiCard
          label="High-risk patients"
          value={num(risk.high ?? 0)}
          hint={`${num(risk.medium ?? 0)} medium risk`}
          tone="bad"
          icon={<AlertTriangle />}
          to={patients}
        />
      </KpiGrid>

      {/* 2. Insights */}
      <SectionHeader title="Insights" description="The comparison that matters most, and what stands out in the latest cycle." />
      <BentoGrid>
        <BentoCard
          span="wide"
          icon={<TrendingUp />}
          title="Engine versus earlier outreach"
          description="Like for like: patients already out of medication when contacted."
          note={
            hasEngine
              ? `${num(fair.baseline.sent)} earlier touches versus ${num(fair.engine.sent)} engine touches. The engine concentrates on these harder cases, so comparing with all earlier outreach would be unfair to it.`
              : undefined
          }
        >
          {hasEngine ? (
            <div className={CHART_H}>
              <ResponsiveContainer>
                <BarChart data={fairRows} barGap={4} barCategoryGap="30%" margin={{ top: 20, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                  <XAxis dataKey="metric" tick={axis} tickLine={false} axisLine={false} />
                  <YAxis tickFormatter={percent} tick={axis} tickLine={false} axisLine={false} width={40} />
                  <Tooltip formatter={(v: number) => pct(v, 1)} {...tooltipProps} />
                  <Legend {...legendProps} />
                  <Bar isAnimationActive={false} dataKey="baseline" name="Earlier outreach" fill={BASELINE} radius={[4, 4, 0, 0]} maxBarSize={56}>
                    <LabelList dataKey="baseline" position="top" formatter={(v: number) => pct(v, 1)} style={axis} />
                  </Bar>
                  <Bar isAnimationActive={false} dataKey="engine" name="Engine recommendations" fill={ENGINE} radius={[4, 4, 0, 0]} maxBarSize={56}>
                    <LabelList dataKey="engine" position="top" formatter={(v: number) => pct(v, 1)} style={axis} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="flex h-full flex-col gap-5">
              <dl className="grid grid-cols-2 gap-3">
                {[
                  { label: "Earlier outreach: responded", value: pct(fair.baseline?.response_rate, 1) },
                  { label: "Earlier outreach: led to a refill", value: pct(fair.baseline?.fill_rate, 1) },
                ].map((f) => (
                  <div key={f.label} className="rounded-lg bg-subtle px-4 py-3.5">
                    <dt className="text-[13px] leading-5 text-ink-muted">{f.label}</dt>
                    <dd className="tabular mt-1 text-[28px] font-semibold leading-9 text-ink">
                      <AnimatedNumber value={f.value} />
                    </dd>
                  </div>
                ))}
              </dl>
              <p className="text-sm leading-6 text-ink-muted">
                Baseline from {num(fair.baseline?.sent)} earlier touches to patients already in a gap. The engine bars appear
                once at least {MIN_SENT} engine recommendations of this kind have an outcome
                {can(P.ENGINE_OPERATE)
                  ? ": approve and send recommendations, then play out the responses on the Engine page."
                  : ", as the care team and representatives send recommendations and recipients respond."}
              </p>
              {can(P.ENGINE_OPERATE) && (
                <Link
                  to="/admin"
                  className="group mt-auto inline-flex items-center gap-1.5 self-start text-sm font-semibold text-primary-ink hover:underline"
                >
                  Open the Engine page
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
                </Link>
              )}
            </div>
          )}
        </BentoCard>

        <BentoCard span="narrow" icon={<Lightbulb />} title="What stands out" description="Derived from the figures on this page.">
          <ul className="divide-y divide-line">
            <InsightRow icon={<AlertTriangle />} tone="bad" figure={num(risk.high ?? 0)}>
              Patients at high adherence risk, with {pct(all?.in_gap_rate, 0)} of therapies currently without supply.
            </InsightRow>
            {topGate && (
              <InsightRow icon={<ShieldAlert />} tone="warn" figure={num(topGate.count)}>
                Most common reason an option was held back: {topGate.label.charAt(0).toLowerCase() + topGate.label.slice(1)}.
              </InsightRow>
            )}
            {patientBest && (
              <InsightRow icon={<MessageSquare />} tone="brand" figure={pct(patientBest.engine ?? patientBest.baseline)}>
                Patients respond most to {patientBest.channel.toLowerCase()}.
              </InsightRow>
            )}
            {hcpBest && (
              <InsightRow icon={<Stethoscope />} tone="brand" figure={pct(hcpBest.engine ?? hcpBest.baseline)}>
                HCPs respond most to {hcpBest.channel.toLowerCase()}.
              </InsightRow>
            )}
          </ul>
        </BentoCard>
      </BentoGrid>

      {/* 3. Recommendations */}
      <SectionHeader title="Recommendations" description="Where every recommendation stands, and what the engine is proposing." />
      <BentoGrid>
        <BentoCard span="half" icon={<ListChecks />} title="Status by audience" description="Every recommendation on record." link={queue ? { to: queue, label: "Open queue" } : undefined}>
          <div className="space-y-6">
            <StatusBar rows={status} target="PATIENT" label="Patients" />
            <StatusBar rows={status} target="HCP" label="HCPs" />
          </div>
        </BentoCard>
        <BentoCard span="half" icon={<BarChart3 />} title="What the engine is recommending" description="Latest cycle, top actions by audience and channel.">
          {mix.length ? (
            <div className="grid gap-6 sm:grid-cols-2">
              {(["PATIENT", "HCP"] as const).map((target) => (
                <div key={target} className="min-w-0">
                  <h4 className="mb-3 text-[13px] font-semibold text-ink-muted">{target === "PATIENT" ? "Patients" : "HCPs"}</h4>
                  <ul className="space-y-3">
                    {mixFor(target).map((m, i) => (
                      <BarRow key={i} label={titleCase(m.action)} sub={titleCase(m.channel)} figure={num(m.count)} value={m.count / mixMax} />
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <Empty>No recommendations in the latest cycle.</Empty>
          )}
        </BentoCard>
      </BentoGrid>

      {/* 4. Adherence and engagement */}
      <SectionHeader title="Adherence and engagement" description="Days covered by measure over time, and which channels people respond to." />
      <BentoGrid>
        {/* With a trend, the chart leads and today's split sits beside it. With one date only,
            there is no line to draw, so today's split takes the full row instead of leaving a
            mostly empty chart card. */}
        {trendRows.length > 1 && (
          <BentoCard span="wide" icon={<Activity />} title="Adherent share over time" description="Therapies with days covered at or above the threshold, by measure.">
            <div className={CHART_H}>
              <ResponsiveContainer>
                <LineChart data={trendRows} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                  <XAxis dataKey="date" tick={axis} tickLine={false} axisLine={false} minTickGap={16} />
                  <YAxis tickFormatter={(v: number) => pct(v, 0)} domain={["auto", "auto"]} tick={axis} tickLine={false} axisLine={false} width={44} />
                  <Tooltip formatter={(v: number) => pct(v, 1)} {...tooltipProps} />
                  <Legend {...legendProps} />
                  {measures.map((m) => (
                    <Line isAnimationActive={false} key={m} dataKey={m} name={titleCase(m)} stroke={MEASURE_COLOR[m]} strokeWidth={2.25} dot={{ r: 3.5, strokeWidth: 0, fill: MEASURE_COLOR[m] }} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </BentoCard>
        )}
        <BentoCard
          span={trendRows.length > 1 ? "narrow" : "full"}
          icon={<HeartPulse />}
          title="Adherence by measure"
          description={`Adherent share today; target ${pct(d.adherence.pdc_threshold)} of days covered.`}
          note={trendRows.length > 1 ? undefined : "One measurement date so far. A trend appears as cycles run on later days."}
        >
          <ul className={cx("gap-x-10 gap-y-4", trendRows.length > 1 ? "space-y-4" : "grid md:grid-cols-3")}>
            {byMeasure.map((c) => (
              <BarRow
                key={c.measure}
                label={titleCase(c.measure)}
                figure={pct(c.adherent_rate, 1)}
                value={c.adherent_rate}
                tone="ok"
                sub={`${num(c.therapies)} therapies · ${pct(c.in_gap_rate, 1)} in a gap · mean ${pct(c.mean_pdc, 0)} covered`}
              />
            ))}
          </ul>
        </BentoCard>
        {(["PATIENT", "HCP"] as const).map((target) => (
          <BentoCard
            key={target}
            span="half"
            icon={target === "PATIENT" ? <HeartPulse /> : <Stethoscope />}
            title={`${target === "PATIENT" ? "Patient" : "HCP"} response rate by channel`}
            description={`All ${target === "PATIENT" ? "patient" : "HCP"} outreach with a known outcome.`}
            note={`Engine bars appear for a channel once at least ${MIN_SENT} sent recommendations have an outcome.`}
          >
            <div className={CHART_H}>
              <ResponsiveContainer>
                <BarChart data={channelRows(target)} barGap={3} barCategoryGap="26%" margin={{ top: 20, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                  <XAxis dataKey="channel" tick={axis} tickLine={false} axisLine={false} interval={0} />
                  <YAxis tickFormatter={percent} tick={axis} tickLine={false} axisLine={false} width={40} />
                  <Tooltip formatter={(v: number) => pct(v, 1)} {...tooltipProps} />
                  <Legend {...legendProps} />
                  <Bar isAnimationActive={false} dataKey="baseline" name="Earlier outreach" fill={BASELINE} radius={[4, 4, 0, 0]} maxBarSize={32}>
                    <LabelList dataKey="baseline" position="top" formatter={percent} style={axis} />
                  </Bar>
                  <Bar isAnimationActive={false} dataKey="engine" name="Engine recommendations" fill={ENGINE} radius={[4, 4, 0, 0]} maxBarSize={32}>
                    <LabelList dataKey="engine" position="top" formatter={percent} style={axis} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </BentoCard>
        ))}
      </BentoGrid>

      {/* 5. Compliance and safeguards */}
      <SectionHeader title="Compliance and safeguards" description="What the deterministic safeguards stopped. Blocked options are never sent." />
      <BentoGrid>
        <BentoCard span="wide" icon={<ShieldAlert />} title="Why options were held back" description="Latest cycle, every option the engine evaluated, not only the one chosen.">
          {gateReasons.length ? (
            <ul className="space-y-4">
              {gateReasons.map((g) => (
                <BarRow key={g.label} label={g.label} figure={num(g.count)} value={g.count / gateMax} tone="warn" />
              ))}
            </ul>
          ) : (
            <Empty>No option was held back in the latest cycle.</Empty>
          )}
        </BentoCard>
        <BentoCard
          span="narrow"
          icon={<FileCheck2 />}
          title="Safeguard summary"
          link={can(P.CONTENT_READ_ALL) ? { to: "/content", label: "Content" } : undefined}
        >
          <dl className="divide-y divide-line">
            {[
              { label: "Blocked recommendations", value: num(blocked), sub: totalRecs ? `${pct(blocked / totalRecs, 1)} of all recommendations` : undefined, tone: blocked ? "text-bad" : "text-ink" },
              ...(can(P.CONTENT_READ_ALL)
                ? [
                    { label: "Content pending MLR review", value: content.data ? num(pendingContent) : "—", sub: "approval by Compliance only", tone: pendingContent ? "text-warn" : "text-ink" },
                    { label: "Content approval expired", value: content.data ? num(expiredContent) : "—", sub: "cannot be used until renewed", tone: expiredContent ? "text-bad" : "text-ink" },
                  ]
                : []),
            ].map((row) => (
              <div key={row.label} className="flex items-start justify-between gap-4 py-3.5 first:pt-0 last:pb-0">
                <div className="min-w-0">
                  <dt className="text-sm text-ink">{row.label}</dt>
                  {row.sub && <dd className="mt-0.5 text-xs text-ink-subtle">{row.sub}</dd>}
                </div>
                <dd className={cx("tabular text-xl font-semibold", row.tone)}>{row.value}</dd>
              </div>
            ))}
          </dl>
        </BentoCard>
      </BentoGrid>

      {/* 6. Platform operations (administrators) */}
      {can(P.USER_MANAGE) && <PlatformOperations />}

      {/* 7. Recent activity */}
      {can(P.AUDIT_READ) && (
        <>
          <SectionHeader title="Recent activity" description="The latest entries in the audit log." />
          <BentoGrid>
            <BentoCard span="full" icon={<ScrollText />} title="Audit log" link={{ to: "/audit", label: "View all" }}>
              {recent.isLoading ? (
                <LoadingRows rows={4} label="Loading recent activity" />
              ) : recent.error ? (
                <ErrorState error={recent.error} retry={() => void recent.refetch()} title="Recent activity could not be loaded" />
              ) : !recent.data.items.length ? (
                <Empty>No activity recorded yet.</Empty>
              ) : (
                <div className="grid gap-x-10 lg:grid-cols-2">
                  {[recent.data.items.slice(0, 4), recent.data.items.slice(4, 8)].map((col: Json[], i) => (
                    <Timeline key={i}>
                      {col.map((a) => (
                        <TimelineItem
                          key={a.id}
                          tone={/block|reject/.test(a.action) ? "bad" : /approv/.test(a.action) ? "brand" : /sent|respon|fill/.test(a.action) ? "ok" : "neutral"}
                          title={
                            <>
                              <span className="font-semibold">{eventLabel(a.action)}</span>
                              <span className="text-ink-muted"> by {a.actor}</span>
                            </>
                          }
                          meta={fmtDateTime(a.ts)}
                        />
                      ))}
                    </Timeline>
                  ))}
                </div>
              )}
            </BentoCard>
          </BentoGrid>
        </>
      )}
    </>
  );
}
