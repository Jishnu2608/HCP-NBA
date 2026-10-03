import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Ban, CalendarX2, CheckCircle2, ClipboardList, HeartPulse, Stethoscope } from "lucide-react";
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
import { api } from "../api";
import type { Json } from "../api";
import {
  Card,
  ErrorState,
  Loading,
  Meter,
  PageHeader,
  Stat,
  Table,
  fmtDate,
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

function count(rows: Json[], target: string, status: string) {
  return rows.filter((r) => r.target_type === target && r.status === status).reduce((n, r) => n + r.count, 0);
}

function ChartNote({ children }: { children: React.ReactNode }) {
  return <p className="mt-3 text-[13px] leading-5 text-ink-subtle">{children}</p>;
}

export default function Dashboard() {
  const overview = useQuery({ queryKey: ["analytics"], queryFn: () => api("/analytics/overview") });
  if (overview.isLoading) return <Loading label="Calculating metrics" />;
  if (overview.error) return <ErrorState error={overview.error} retry={() => void overview.refetch()} variant="page" title="Metrics could not be loaded" />;
  const d: Json = overview.data;
  const status: Json[] = d.recommendations.by_status;
  const all = d.adherence.current.find((c: Json) => c.measure === "all");
  const risk = d.adherence.patients_by_risk;

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

  const dates = [...new Set(d.adherence.trend.map((t: Json) => t.as_of_date))] as string[];
  const trendRows = dates.map((date) => {
    const row: Json = { date: fmtDate(date).replace(/, \d{4}$/, "") };
    for (const t of d.adherence.trend.filter((x: Json) => x.as_of_date === date)) {
      row[t.measure] = t.adherent_rate;
    }
    return row;
  });
  const measures = ["diabetes", "hypertension", "cholesterol"];
  const gateReasons: Json[] = d.recommendations.gate_reasons;
  const gateMax = Math.max(1, ...gateReasons.map((g) => g.count));
  const mix: Json[] = d.recommendations.mix;
  const blocked = count(status, "PATIENT", "blocked") + count(status, "HCP", "blocked");

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle="What the engine recommended, what the safeguards stopped, and what changed. Aggregates only."
      />

      <h2 className="sr-only">Key figures</h2>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Patient actions ready"
          value={num(count(status, "PATIENT", "ready_for_review"))}
          hint="latest cycle"
          icon={<HeartPulse className="h-4 w-4" aria-hidden />}
        />
        <Stat
          label="HCP actions ready"
          value={num(count(status, "HCP", "ready_for_review"))}
          hint="latest cycle"
          icon={<Stethoscope className="h-4 w-4" aria-hidden />}
        />
        <Stat
          label="Blocked by safeguards"
          value={num(blocked)}
          hint="cannot be sent"
          tone={blocked ? "bad" : undefined}
          icon={<Ban className="h-4 w-4" aria-hidden />}
        />
        <Stat
          label="Adherent therapies"
          value={pct(all?.adherent_rate, 1)}
          hint={`days covered ≥ ${pct(d.adherence.pdc_threshold)}`}
          tone="ok"
          icon={<CheckCircle2 className="h-4 w-4" aria-hidden />}
        />
        <Stat
          label="Currently in a gap"
          value={pct(all?.in_gap_rate, 1)}
          hint="therapies without supply"
          tone="warn"
          icon={<CalendarX2 className="h-4 w-4" aria-hidden />}
        />
        <Stat
          label="High-risk patients"
          value={num(risk.high ?? 0)}
          hint={`${num(risk.medium ?? 0)} medium risk`}
          tone="bad"
          icon={<AlertTriangle className="h-4 w-4" aria-hidden />}
        />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Card
          title="Engine versus earlier outreach"
          description="Like for like: patients already out of medication when contacted."
        >
          {hasEngine ? (
            <>
              <div className="h-64">
                <ResponsiveContainer>
                  <BarChart data={fairRows} barGap={4} barCategoryGap="30%" margin={{ top: 20, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                    <XAxis dataKey="metric" tick={axis} tickLine={false} axisLine={false} />
                    <YAxis tickFormatter={percent} tick={axis} tickLine={false} axisLine={false} width={40} />
                    <Tooltip formatter={(v: number) => pct(v, 1)} {...tooltipProps} />
                    <Legend {...legendProps} />
                    <Bar isAnimationActive={false} dataKey="baseline" name="Earlier outreach" fill={BASELINE} radius={[4, 4, 0, 0]} maxBarSize={48}>
                      <LabelList dataKey="baseline" position="top" formatter={(v: number) => pct(v, 1)} style={axis} />
                    </Bar>
                    <Bar isAnimationActive={false} dataKey="engine" name="Engine recommendations" fill={ENGINE} radius={[4, 4, 0, 0]} maxBarSize={48}>
                      <LabelList dataKey="engine" position="top" formatter={(v: number) => pct(v, 1)} style={axis} />
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <ChartNote>
                {num(fair.baseline.sent)} earlier touches versus {num(fair.engine.sent)} engine touches. The engine
                concentrates on these harder cases, so comparing against all earlier outreach would be unfair to it.
              </ChartNote>
            </>
          ) : (
            <div className="rounded-lg bg-subtle px-4 py-5 text-sm leading-6 text-ink-muted">
              Not enough engine outreach has been answered yet for a fair comparison. Approve and send recommendations,
              then advance the demo clock on the Engine page. Earlier outreach to patients already in a gap:{" "}
              <strong className="text-ink">{pct(fair.baseline?.response_rate, 1)}</strong> responded,{" "}
              <strong className="text-ink">{pct(fair.baseline?.fill_rate, 1)}</strong> led to a refill (
              {num(fair.baseline?.sent)} touches).
            </div>
          )}
        </Card>

        <Card title="Adherent share by measure" description="Therapies with days covered at or above the threshold.">
          {trendRows.length > 1 ? (
            <div className="h-64">
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
          ) : (
            <p className="mb-2 rounded-lg bg-subtle px-4 py-3 text-sm text-ink-muted">
              One measurement date so far. A trend line appears after the demo clock is advanced.
            </p>
          )}
          <div className="mt-4">
            <Table
              caption="Adherence by measure"
              head={["Measure", "Therapies", "Adherent", "Mean days covered", "In a gap"]}
              align={[undefined, "right", "right", "right", "right"]}
            >
              {d.adherence.current.map((c: Json) => (
                <tr key={c.measure} className={c.measure === "all" ? "font-semibold" : undefined}>
                  <td className="whitespace-nowrap text-ink">{c.measure === "all" ? "All measures" : titleCase(c.measure)}</td>
                  <td className="tabular text-right">{num(c.therapies)}</td>
                  <td className="tabular text-right">{pct(c.adherent_rate, 1)}</td>
                  <td className="tabular text-right">{pct(c.mean_pdc, 1)}</td>
                  <td className="tabular text-right">{pct(c.in_gap_rate, 1)}</td>
                </tr>
              ))}
            </Table>
          </div>
        </Card>

        {(["PATIENT", "HCP"] as const).map((target) => (
          <Card
            key={target}
            title={`${target === "PATIENT" ? "Patient" : "HCP"} response rate by channel`}
            description={`All ${target === "PATIENT" ? "patient" : "HCP"} outreach with a known outcome.`}
          >
            <div className="h-64">
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
            <ChartNote>
              Engine bars appear for a channel once at least {MIN_SENT} sent recommendations have an outcome.
            </ChartNote>
          </Card>
        ))}

        <Card
          title="Why options were held back"
          description="Latest cycle, every option the engine evaluated. Blocked options are never sent."
        >
          {gateReasons.length ? (
            <ul className="space-y-3.5">
              {gateReasons.map((g) => (
                <li key={g.label}>
                  <div className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="min-w-0 text-ink">{g.label}</span>
                    <span className="tabular shrink-0 font-semibold text-ink">{num(g.count)}</span>
                  </div>
                  <Meter value={g.count / gateMax} tone="warn" className="mt-1.5" label={g.label} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ink-subtle">No option was held back in the latest cycle.</p>
          )}
        </Card>

        <Card title="What the engine is recommending" description="Latest cycle, by audience, action and channel.">
          {mix.length ? (
            <Table caption="Recommendation mix" head={["Audience", "Action", "Channel", "Count"]} align={[undefined, undefined, undefined, "right"]}>
              {mix.map((m: Json, i: number) => (
                <tr key={i}>
                  <td className="whitespace-nowrap text-ink-muted">{m.target_type === "HCP" ? "HCP" : "Patient"}</td>
                  <td className="text-ink">{titleCase(m.action)}</td>
                  <td className="whitespace-nowrap text-ink-muted">{titleCase(m.channel)}</td>
                  <td className="tabular text-right font-semibold">{num(m.count)}</td>
                </tr>
              ))}
            </Table>
          ) : (
            <p className="flex items-center gap-2 text-sm text-ink-subtle">
              <ClipboardList className="h-4 w-4" aria-hidden /> No recommendations in the latest cycle.
            </p>
          )}
        </Card>
      </div>
    </>
  );
}
