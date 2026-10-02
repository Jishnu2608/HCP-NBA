import { useQuery } from "@tanstack/react-query";
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
import { Card, ErrorNote, Loading, PageHeader, Stat, Table, fmtDate, pct, titleCase } from "../ui";

// Chart colours come from CSS roles so they are defined once (see index.css).
const BASELINE = "#a8a29e"; // neutral: the comparison, not a series of interest
const ENGINE = "var(--series-1)";
const MEASURE_COLOR: Record<string, string> = {
  diabetes: "var(--series-1)",
  hypertension: "var(--series-2)",
  cholesterol: "var(--series-3)",
};
const axis = { fontSize: 12, fill: "var(--chart-text)" };
const percent = (v: number) => `${Math.round(v * 100)}%`;
const tooltipStyle = { fontSize: 12, borderRadius: 8, border: "1px solid #e7e5e4" };

// A rate from a handful of sends is noise: show the engine bar only with enough volume.
const MIN_SENT = 20;
const engineRate = (row: Json | undefined) => (row && row.sent >= MIN_SENT ? row.response_rate : undefined);

function count(rows: Json[], target: string, status: string) {
  return rows.filter((r) => r.target_type === target && r.status === status).reduce((n, r) => n + r.count, 0);
}

export default function Dashboard() {
  const overview = useQuery({ queryKey: ["analytics"], queryFn: () => api("/analytics/overview") });
  if (overview.isLoading) return <Loading label="Calculating metrics" />;
  if (overview.error) return <ErrorNote error={overview.error} />;
  const d: Json = overview.data;
  const status: Json[] = d.recommendations.by_status;
  const all = d.adherence.current.find((c: Json) => c.measure === "all");
  const risk = d.adherence.patients_by_risk;

  const fair = Object.fromEntries(d.engagement.like_for_like.map((r: Json) => [r.source, r]));
  const fairRows = [
    { metric: "Responded", baseline: fair.baseline?.response_rate, engine: fair.engine?.response_rate },
    { metric: "Led to a fill", baseline: fair.baseline?.fill_rate, engine: fair.engine?.fill_rate },
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
    const row: Json = { date: fmtDate(date).replace(/ \d{4}$/, "") };
    for (const t of d.adherence.trend.filter((x: Json) => x.as_of_date === date)) {
      row[t.measure] = t.adherent_rate;
    }
    return row;
  });
  const measures = ["diabetes", "hypertension", "cholesterol"];

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle="What the engine recommended, what the gates stopped, and what changed. Aggregates only."
      />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Patient actions ready" value={count(status, "PATIENT", "ready_for_review").toLocaleString()} hint="latest cycle" />
        <Stat label="HCP actions ready" value={count(status, "HCP", "ready_for_review")} hint="latest cycle" />
        <Stat
          label="Blocked by gates"
          value={count(status, "PATIENT", "blocked") + count(status, "HCP", "blocked")}
          hint="cannot be sent"
        />
        <Stat label="Adherent therapies" value={pct(all?.adherent_rate, 1)} hint={`days covered ≥ ${pct(d.adherence.pdc_threshold)}`} />
        <Stat label="Currently in a gap" value={pct(all?.in_gap_rate, 1)} hint="therapies without supply" />
        <Stat label="High-risk patients" value={(risk.high ?? 0).toLocaleString()} hint={`${(risk.medium ?? 0).toLocaleString()} medium`} />
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <Card title="Engine versus earlier outreach, like for like">
          {hasEngine ? (
            <>
              <div className="h-56">
                <ResponsiveContainer>
                  <BarChart data={fairRows} barGap={2} barCategoryGap="30%" margin={{ top: 16, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                    <XAxis dataKey="metric" tick={axis} tickLine={false} axisLine={false} />
                    <YAxis tickFormatter={percent} tick={axis} tickLine={false} axisLine={false} width={40} />
                    <Tooltip formatter={(v: number) => pct(v, 1)} contentStyle={tooltipStyle} cursor={{ fill: "#f5f5f4" }} />
                    <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                    <Bar isAnimationActive={false} dataKey="baseline" name="Earlier outreach" fill={BASELINE} radius={[4, 4, 0, 0]} maxBarSize={44}>
                      <LabelList dataKey="baseline" position="top" formatter={(v: number) => pct(v, 1)} style={axis} />
                    </Bar>
                    <Bar isAnimationActive={false} dataKey="engine" name="Engine recommendations" fill={ENGINE} radius={[4, 4, 0, 0]} maxBarSize={44}>
                      <LabelList dataKey="engine" position="top" formatter={(v: number) => pct(v, 1)} style={axis} />
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <p className="mt-2 text-xs text-stone-500">
                Patient outreach sent while the patient was already out of medication: {fair.baseline.sent.toLocaleString()} earlier
                touches versus {fair.engine.sent.toLocaleString()} engine touches. The engine concentrates on these harder
                cases, so comparing against all earlier outreach would be unfair to it.
              </p>
            </>
          ) : (
            <p className="text-sm text-stone-500">
              Not enough engine outreach has been answered yet for a fair comparison. Approve and send recommendations, then advance the demo
              clock from the Engine page. Earlier outreach to patients already in a gap: {pct(fair.baseline?.response_rate, 1)}{" "}
              responded, {pct(fair.baseline?.fill_rate, 1)} led to a fill ({fair.baseline?.sent.toLocaleString()} touches).
            </p>
          )}
        </Card>

        <Card title="Adherent share by measure (days covered at or above threshold)">
          {trendRows.length > 1 ? (
            <div className="h-56">
              <ResponsiveContainer>
                <LineChart data={trendRows} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                  <XAxis dataKey="date" tick={axis} tickLine={false} axisLine={false} />
                  <YAxis tickFormatter={(v: number) => pct(v, 1)} domain={["auto", "auto"]} tick={axis} tickLine={false} axisLine={false} width={48} />
                  <Tooltip formatter={(v: number) => pct(v, 1)} contentStyle={tooltipStyle} />
                  <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                  {measures.map((m) => (
                    <Line isAnimationActive={false} key={m} dataKey={m} name={titleCase(m)} stroke={MEASURE_COLOR[m]} strokeWidth={2} dot={{ r: 4 }} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="mb-3 text-sm text-stone-500">
              One measurement date so far. A trend line appears after the demo clock is advanced.
            </p>
          )}
          <Table head={["Measure", "Therapies", "Adherent", "Mean days covered", "In a gap now"]}>
            {d.adherence.current.map((c: Json) => (
              <tr key={c.measure}>
                <td className="px-3 py-2 font-medium">{c.measure === "all" ? "All measures" : titleCase(c.measure)}</td>
                <td className="tabular px-3 py-2">{c.therapies.toLocaleString()}</td>
                <td className="tabular px-3 py-2">{pct(c.adherent_rate, 1)}</td>
                <td className="tabular px-3 py-2">{pct(c.mean_pdc, 1)}</td>
                <td className="tabular px-3 py-2">{pct(c.in_gap_rate, 1)}</td>
              </tr>
            ))}
          </Table>
        </Card>

        {(["PATIENT", "HCP"] as const).map((target) => (
          <Card key={target} title={`${target === "PATIENT" ? "Patient" : "HCP"} response rate by channel`}>
            <div className="h-56">
              <ResponsiveContainer>
                <BarChart data={channelRows(target)} barGap={2} barCategoryGap="28%" margin={{ top: 16, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                  <XAxis dataKey="channel" tick={axis} tickLine={false} axisLine={false} />
                  <YAxis tickFormatter={percent} tick={axis} tickLine={false} axisLine={false} width={40} />
                  <Tooltip formatter={(v: number) => pct(v, 1)} contentStyle={tooltipStyle} cursor={{ fill: "#f5f5f4" }} />
                  <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                  <Bar isAnimationActive={false} dataKey="baseline" name="Earlier outreach" fill={BASELINE} radius={[4, 4, 0, 0]} maxBarSize={32}>
                    <LabelList dataKey="baseline" position="top" formatter={percent} style={axis} />
                  </Bar>
                  <Bar isAnimationActive={false} dataKey="engine" name="Engine recommendations" fill={ENGINE} radius={[4, 4, 0, 0]} maxBarSize={32}>
                    <LabelList dataKey="engine" position="top" formatter={percent} style={axis} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-2 text-xs text-stone-500">
              All {target === "PATIENT" ? "patient" : "HCP"} outreach with a known outcome. Engine bars appear for a channel once
              at least {MIN_SENT} sent recommendations have an outcome.
            </p>
          </Card>
        ))}

        <Card title="Why options were held back (latest cycle, all candidates)">
          <div style={{ height: 40 + d.recommendations.gate_reasons.length * 34 }}>
            <ResponsiveContainer>
              <BarChart data={d.recommendations.gate_reasons} layout="vertical" margin={{ top: 0, right: 48, left: 8, bottom: 0 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="label" width={290} tick={axis} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "#f5f5f4" }} />
                <Bar isAnimationActive={false} dataKey="count" name="Candidates" fill={ENGINE} radius={[0, 4, 4, 0]} maxBarSize={18}>
                  <LabelList dataKey="count" position="right" style={axis} formatter={(v: number) => v.toLocaleString()} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-2 text-xs text-stone-500">
            Counts every option the engine evaluated, not only the one chosen. Gated options are never sent.
          </p>
        </Card>

        <Card title="What the engine is recommending (latest cycle)">
          <Table head={["Audience", "Action", "Channel", "Count"]}>
            {d.recommendations.mix.map((m: Json, i: number) => (
              <tr key={i}>
                <td className="px-3 py-2 text-stone-600">{m.target_type === "HCP" ? "HCP" : "Patient"}</td>
                <td className="px-3 py-2">{titleCase(m.action)}</td>
                <td className="px-3 py-2 text-stone-600">{titleCase(m.channel)}</td>
                <td className="tabular px-3 py-2">{m.count}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
    </>
  );
}
