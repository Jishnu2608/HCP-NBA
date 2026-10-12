// Measure and learn (field guide §03.8): STAR-style adherence measures and persistence, what
// worked for whom, how the models changed, and the synthetic holdout. Every figure comes from
// /api/analytics/* with its sample size; observed, estimated and compared numbers are labelled
// as such, and small groups are never shown as rates.
import { useQuery } from "@tanstack/react-query";
import { Beaker, BrainCircuit, FlaskConical, Gauge, Info, ListChecks, Target, TrendingUp } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, query } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { P } from "../permissions";
import {
  Alert,
  Badge,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingRows,
  PageHeader,
  Segmented,
  Select,
  Toolbar,
  fmtDate,
  fmtDateTime,
  num,
  pct,
  useShowMore,
} from "../ui";
import type { Column } from "../ui";
import { BentoCard, BentoGrid } from "../layout";
import { Sparkline } from "../charts";

type Tab = "measures" | "learning" | "models" | "holdout";

/** "61% (n = 240)", or why there is no rate. */
function Rate({ figure, n, minN }: { figure: Json | null | undefined; n?: number; minN?: number }) {
  if (!figure) return <span className="text-ink-subtle">—</span>;
  const count = n ?? figure.n;
  if (figure.rate === null || figure.rate === undefined) {
    return (
      <span className="text-[13px] text-ink-subtle">
        {figure.suppressed ? "Suppressed" : "Too few"} (n = {num(count)}
        {minN ? `, needs ${minN}` : ""})
      </span>
    );
  }
  return (
    <span className="tabular">
      <span className="font-semibold text-ink">{pct(figure.rate, 1)}</span>
      {figure.ci && (
        <span className="text-[13px] text-ink-subtle">
          {" "}
          ({pct(figure.ci[0])}–{pct(figure.ci[1])})
        </span>
      )}
      {count !== undefined && <span className="text-[13px] text-ink-subtle"> · n = {num(count)}</span>}
    </span>
  );
}

export function OutcomesPage() {
  const { can } = useAuth();
  const analyst = can(P.ANALYTICS_READ);
  const [tab, setTab] = useState<Tab>("measures");
  const tabs: Array<{ value: Tab; label: string }> = [
    { value: "measures", label: "Measures" },
    ...(analyst
      ? ([
          { value: "learning", label: "What worked" },
          { value: "models", label: "Models" },
          { value: "holdout", label: "Holdout" },
        ] as Array<{ value: Tab; label: string }>)
      : []),
  ];
  return (
    <>
      <PageHeader
        title={analyst ? "Outcomes and learning" : "Quality measures"}
        subtitle={
          analyst
            ? "What the engagement loop changed, for whom, and how sure we can be. Observed outcomes, model estimates and comparisons are labelled separately."
            : "Adherence measures and persistence for the patients on your panel, in groups large enough to report."
        }
      />
      {tabs.length > 1 && (
        <div className="mb-6 max-w-full overflow-x-auto pb-1">
          <Segmented label="View" options={tabs} value={tab} onChange={setTab} />
        </div>
      )}
      {tab === "measures" && <Measures />}
      {tab === "learning" && analyst && <Learning />}
      {tab === "models" && analyst && <Models />}
      {tab === "holdout" && analyst && <Holdout />}
    </>
  );
}

/* ------------------------------------------------------------------ measures */

function Measures() {
  const q = useQuery({ queryKey: ["measures"], queryFn: () => api("/analytics/measures") });
  if (q.isLoading) return <LoadingRows rows={6} />;
  if (q.error) return <ErrorState error={q.error} retry={() => void q.refetch()} title="Measures could not be loaded" />;
  const d: Json = q.data;
  return (
    <>
      <Alert tone="info" title="Calculated measures, not a Star Rating" className="mb-6">
        STAR-style medication adherence measures calculated on this system's data ({num(d.patients)} patients,{" "}
        {num(d.synthetic_patients)} of them synthetic). An official rating needs the CMS specification, the full plan
        population, case-mix adjustment and cut points, none of which is applied here.
        {d.scope === "panel" && " Your panel only."}
      </Alert>
      <BentoGrid>
        {d.measures.map((m: Json) => (
          <MeasureCard key={m.measure} m={m} d={d} />
        ))}
        <BentoCard span="full" icon={<ListChecks />} title="Definitions">
          <dl className="grid gap-x-8 gap-y-4 text-sm md:grid-cols-2">
            <div>
              <dt className="font-semibold text-ink">Measure rate</dt>
              <dd className="mt-1 text-ink-muted">
                Share of therapies in the denominator with PDC at or above {pct(d.pdc_threshold)} over the last{" "}
                {d.period_days} days (from the first fill in that period). Denominator: confirmed therapies with at least two
                fills on different days, the first at least 91 days ago, and a fill inside the period.
              </dd>
            </div>
            <div>
              <dt className="font-semibold text-ink">Persistence</dt>
              <dd className="mt-1 text-ink-muted">
                A course continues from the first fill until the supply runs out and no refill follows within{" "}
                {d.persistence_gap_days} days. Shares at 180 and 365 days count only courses that started at least that long
                ago.
              </dd>
            </div>
            <div>
              <dt className="font-semibold text-ink">Engine contact</dt>
              <dd className="mt-1 text-ink-muted">
                Therapies whose patient received engine outreach about them in the period, against those that did not. The
                engine contacts patients who need it most, so this is a description, not an effect.
              </dd>
            </div>
            <div>
              <dt className="font-semibold text-ink">Not modelled</dt>
              <dd className="mt-1 text-ink-muted">
                <ul className="list-disc space-y-0.5 pl-5">
                  {d.not_modelled.map((x: string) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
              </dd>
            </div>
          </dl>
          <p className="mt-4 text-[13px] text-ink-subtle">
            Groups under {d.min_group} are suppressed. As of {fmtDate(d.as_of)}. One patient's own figures are on their
            Patient 360 page.
          </p>
        </BentoCard>
      </BentoGrid>
    </>
  );
}

function MeasureCard({ m, d }: { m: Json; d: Json }) {
  const points = m.trend.filter((t: Json) => t.rate !== null);
  return (
    <BentoCard span="narrow" icon={<Target />} title={m.label} description={`Medication adherence, PDC ${pct(d.pdc_threshold)}+`}>
      <div className="flex items-baseline gap-2">
        <span className="tabular text-3xl font-semibold text-ink">
          {m.current.rate === null ? "—" : pct(m.current.rate, 1)}
        </span>
        <span className="text-[13px] text-ink-subtle">
          {m.current.suppressed ? `suppressed, n = ${m.current.n}` : `of ${num(m.current.n)} therapies`}
        </span>
      </div>
      {points.length >= 3 ? (
        <div className="mt-3">
          <Sparkline
            label={`${m.label}: measure rate at recent month ends`}
            domain={[0, 1]}
            points={points.map((t: Json) => ({
              key: t.as_of,
              value: t.rate,
              tip: { title: fmtDate(t.as_of), lines: [`${pct(t.rate, 1)} of ${t.n} therapies`] },
            }))}
          />
          <div className="mt-1 flex justify-between text-xs text-ink-subtle">
            <span>{fmtDate(points[0].as_of)}</span>
            <span>{fmtDate(points[points.length - 1].as_of)}</span>
          </div>
        </div>
      ) : (
        <p className="mt-3 text-[13px] text-ink-subtle">Not enough reportable months for a trend yet.</p>
      )}
      <dl className="mt-4 space-y-2 text-sm">
        <div className="flex justify-between gap-3">
          <dt className="text-ink-muted">Engine-contacted</dt>
          <dd>
            <Rate figure={m.contacted} />
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-ink-muted">Not contacted</dt>
          <dd>
            <Rate figure={m.not_contacted} />
          </dd>
        </div>
        {m.persistence.map((p: Json) => (
          <div key={p.days} className="flex justify-between gap-3">
            <dt className="text-ink-muted">Persistent at {p.days} days</dt>
            <dd>
              <Rate figure={p} />
            </dd>
          </div>
        ))}
        <div className="flex justify-between gap-3">
          <dt className="text-ink-muted">Still on therapy</dt>
          <dd>
            <Rate figure={m.currently_persistent} />
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-ink-muted">Median days to stopping</dt>
          <dd className="tabular font-semibold text-ink">{m.median_days_to_discontinuation ?? "—"}</dd>
        </div>
      </dl>
    </BentoCard>
  );
}

/* ------------------------------------------------------------------ what worked */

const DIMENSION_LABEL: Record<string, string> = {
  situation: "Situation at the time",
  measure: "Therapy area",
  plan_type: "Plan type",
  segment: "Value segment",
  specialty: "Specialty",
};

function Learning() {
  const [audience, setAudience] = useState<"PATIENT" | "HCP">("PATIENT");
  const [segmentBy, setSegmentBy] = useState("situation");
  const [source, setSource] = useState<"all" | "engine" | "earlier">("all");
  const by = audience === "HCP" && segmentBy === "situation" ? "segment" : segmentBy;
  const q = useQuery({
    queryKey: ["learning", audience, by, source],
    queryFn: () => api(`/analytics/learning${query({ audience, segment_by: by, source })}`),
    placeholderData: (previous) => previous,
  });
  const d: Json | undefined = q.data;
  const fill = audience === "PATIENT";
  const columns: Column<Json>[] = [
    { key: "segment", header: DIMENSION_LABEL[by] ?? "Segment", primary: true, cell: (r) => r.segment_label },
    { key: "action", header: "Action", cell: (r) => r.action_label },
    { key: "channel", header: "Channel", cell: (r) => r.channel_label },
    { key: "n", header: "Touches", align: "right", cell: (r) => <span className="tabular">{num(r.n)}</span> },
    { key: "response", header: "Responded", cell: (r) => <Rate figure={r.response} n={r.n} minN={d?.min_n} /> },
    ...(fill ? [{ key: "fill", header: "Refilled", cell: (r: Json) => <Rate figure={r.fill} n={r.n} minN={d?.min_n} /> }] : []),
  ];
  // Rated combinations first (most touches first), then those with too few touches to rate.
  const ordered = d ? [...d.rows].sort((a: Json, b: Json) => Number(b.n >= d.min_n) - Number(a.n >= d.min_n) || b.n - a.n) : [];
  const [visibleRows, moreRows] = useShowMore(ordered, 12, "combinations");
  return (
    <>
      <Toolbar className="mb-4">
        <Segmented
          label="Audience"
          options={[
            { value: "PATIENT", label: "Patients" },
            { value: "HCP", label: "HCPs" },
          ]}
          value={audience}
          onChange={(v) => {
            setAudience(v);
            setSegmentBy(v === "HCP" ? "segment" : "situation");
          }}
        />
        <Select label="Segment by" value={by} onChange={(e) => setSegmentBy(e.target.value)}>
          {(d?.dimensions ?? (audience === "HCP" ? ["segment", "specialty"] : ["situation", "measure", "plan_type"])).map(
            (k: string) => (
              <option key={k} value={k}>
                {DIMENSION_LABEL[k] ?? k}
              </option>
            ),
          )}
        </Select>
        <Segmented
          label="Touches"
          options={[
            { value: "all", label: "All" },
            { value: "engine", label: "Engine" },
            { value: "earlier", label: "Earlier outreach" },
          ]}
          value={source}
          onChange={setSource}
        />
      </Toolbar>
      {q.isLoading ? (
        <LoadingRows rows={8} />
      ) : q.error ? (
        <ErrorState error={q.error} retry={() => void q.refetch()} title="Results could not be loaded" />
      ) : (
        <>
          <p className="mb-4 flex items-start gap-2 text-[13px] text-ink-muted">
            <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            Observed outcomes over the last 12 months ({num(d!.touches)} touches). Rates need at least {d!.min_n} touches;
            ranges are 95% intervals. Groups differ in who was contacted and when, so this shows what happened, not what
            caused it.
          </p>
          {d!.best.length > 0 && (
            <BentoGrid className="mb-6">
              {d!.best.map((b: Json) => (
                <BentoCard key={b.segment} span="narrow" icon={<TrendingUp />} title={b.segment_label} description="Best observed so far">
                  <div className="text-sm font-semibold text-ink">
                    {b.action_label.charAt(0).toUpperCase() + b.action_label.slice(1)} by {b.channel_label}
                  </div>
                  <div className="mt-1 text-sm">
                    <Rate figure={b[d!.metric]} n={b.n} />
                  </div>
                  <div className="mt-1 text-[13px] text-ink-subtle">{d!.metric === "fill" ? "refilled after the touch" : "responded"}</div>
                </BentoCard>
              ))}
            </BentoGrid>
          )}
          <Card title="By segment, action and channel" description="Rated combinations first; every combination is listed, including those with too few touches to rate.">
            {d!.rows.length ? (
              <>
                <DataTable
                  columns={columns}
                  rows={visibleRows}
                  rowKey={(r) => `${r.segment}-${r.action}-${r.channel}`}
                  caption="Observed outcomes by segment, action and channel"
                />
                {moreRows}
              </>
            ) : (
              <EmptyState title="No completed touches yet" icon={<Beaker className="h-5 w-5" />}>
                Outcomes appear here once outreach has been sent and responses captured.
              </EmptyState>
            )}
          </Card>
          <Card title="Month by month" description="Engine touches against earlier outreach, as observed." className="mt-6">
            <DataTable
              columns={[
                { key: "month", header: "Month", primary: true, cell: (r: Json) => r.month },
                { key: "source", header: "Touches", cell: (r: Json) => (r.source === "engine" ? "Engine" : "Earlier outreach") },
                { key: "n", header: "Count", align: "right", cell: (r: Json) => <span className="tabular">{num(r.n)}</span> },
                { key: "r", header: "Responded", cell: (r: Json) => <Rate figure={r.response} n={r.n} minN={d!.min_n} /> },
                ...(fill ? [{ key: "f", header: "Refilled", cell: (r: Json) => <Rate figure={r.fill} n={r.n} minN={d!.min_n} /> }] : []),
              ]}
              rows={[...d!.trend].reverse()}
              rowKey={(r) => `${r.month}-${r.source}`}
              caption="Observed outcomes by month"
            />
          </Card>
        </>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ models */

const MODEL_LABEL: Record<string, string> = {
  patient_engage: "Patient response",
  patient_fill: "Patient refill",
  hcp_engage: "HCP engagement",
};

function Models() {
  const q = useQuery({ queryKey: ["analytics-models"], queryFn: () => api("/analytics/models") });
  if (q.isLoading) return <LoadingRows rows={6} />;
  if (q.error) return <ErrorState error={q.error} retry={() => void q.refetch()} title="Models could not be loaded" />;
  const d: Json = q.data;
  return (
    <>
      <p className="mb-4 flex items-start gap-2 text-[13px] text-ink-muted">
        <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        Each retrain registers a new version. AUC is measured on held-back training rows. "Observed after" counts the engine
        touches sent while that version was in use: different periods and people, so not a controlled comparison.
      </p>
      <BentoGrid>
        {d.models.map((m: Json) => (
          <BentoCard key={m.name} span="full" icon={<BrainCircuit />} title={MODEL_LABEL[m.name] ?? m.name}>
            <DataTable
              columns={[
                {
                  key: "v",
                  header: "Version",
                  primary: true,
                  cell: (v: Json) => (
                    <span className="flex items-center gap-2">
                      v{v.version} {v.is_active && <Badge tone="ok">In use</Badge>}
                    </span>
                  ),
                },
                { key: "t", header: "Trained", cell: (v: Json) => fmtDateTime(v.trained_ts) },
                {
                  key: "auc",
                  header: "AUC (baseline)",
                  cell: (v: Json) => (
                    <span className="tabular">
                      {v.auc?.toFixed(3) ?? "—"} <span className="text-ink-subtle">({v.auc_baseline?.toFixed(3) ?? "—"})</span>
                    </span>
                  ),
                },
                {
                  key: "chg",
                  header: "Change",
                  cell: (v: Json) =>
                    v.auc_change === null ? "—" : <span className="tabular">{v.auc_change > 0 ? "+" : ""}{v.auc_change.toFixed(3)}</span>,
                },
                { key: "n", header: "Training rows", align: "right", cell: (v: Json) => <span className="tabular">{num((v.n_train ?? 0) + (v.n_test ?? 0))}</span> },
                {
                  key: "obs",
                  header: "Observed after",
                  cell: (v: Json) => (
                    <span className="text-sm">
                      <Rate figure={v.observed_response} n={v.engine_touches} /> responded
                      {v.observed_fill && (
                        <>
                          <br />
                          <Rate figure={v.observed_fill} n={v.engine_touches} /> refilled
                        </>
                      )}
                    </span>
                  ),
                },
              ]}
              rows={[...m.versions].reverse()}
              rowKey={(v) => v.version}
              caption={`${m.name} versions`}
            />
          </BentoCard>
        ))}
        <BentoCard span="full" icon={<Gauge />} title="Estimated against observed" description="The model estimate stored on each engine recommendation when it was made, beside what happened.">
          {d.calibration.length ? (
            <DataTable
              columns={[
                { key: "a", header: "Audience", primary: true, cell: (r: Json) => (r.audience === "HCP" ? "HCP" : "Patient") },
                { key: "act", header: "Action", cell: (r: Json) => r.action_label },
                { key: "n", header: "Touches", align: "right", cell: (r: Json) => <span className="tabular">{num(r.n)}</span> },
                { key: "e", header: "Estimated", cell: (r: Json) => (r.estimated === null ? "Too few" : pct(r.estimated, 1)) },
                { key: "o", header: `Observed`, cell: (r: Json) => <Rate figure={r.observed} n={r.n} /> },
              ]}
              rows={d.calibration}
              rowKey={(r) => `${r.audience}-${r.action}`}
              caption="Estimated against observed outcomes"
            />
          ) : (
            <EmptyState title="No engine touches with a captured outcome yet" icon={<Gauge className="h-5 w-5" />} />
          )}
        </BentoCard>
      </BentoGrid>
    </>
  );
}

/* ------------------------------------------------------------------ holdout */

function Holdout() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["holdout"], queryFn: () => api("/analytics/holdout") });
  if (q.isLoading) return <LoadingRows rows={4} />;
  if (q.error) return <ErrorState error={q.error} retry={() => void q.refetch()} title="The holdout could not be loaded" />;
  const d: Json = q.data;
  return (
    <>
      <Alert tone="info" title="Randomised holdout, synthetic patients only" className="mb-6">
        {d.basis} Real patients are never held out.
      </Alert>
      {d.status === "not_started" ? (
        <Card>
          <EmptyState title="The holdout has not run yet" icon={<FlaskConical className="h-5 w-5" />}>
            It is {d.enabled ? "on and applies at the next engine cycle" : "off"}. {d.enabled ? "" : "An administrator can switch it on"}
            {!d.enabled && can(P.CONFIG_MANAGE) ? (
              <>
                {" "}
                with the <code>holdout</code> setting on the <Link to="/admin" className="font-semibold text-primary-ink underline">Engine</Link> page.
              </>
            ) : (
              "."
            )}{" "}
            A share of {pct(d.share)} of synthetic patients then receives no engine outreach.
          </EmptyState>
        </Card>
      ) : (
        <BentoGrid>
          {d.groups.map((g: Json) => (
            <BentoCard key={g.group} span="half" icon={<FlaskConical />} title={g.label}>
              <dl className="space-y-2 text-sm">
                <div className="flex justify-between gap-3">
                  <dt className="text-ink-muted">Patients · therapies</dt>
                  <dd className="tabular font-semibold text-ink">
                    {num(g.patients)} · {num(g.therapies)}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-ink-muted">At PDC 80%+ since the start</dt>
                  <dd>
                    <Rate figure={g.adherent} n={g.therapies} minN={d.min_n} />
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-ink-muted">Refills per therapy since the start</dt>
                  <dd className="tabular font-semibold text-ink">{g.fills_per_therapy ?? "—"}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-ink-muted">Engine contacts since the start</dt>
                  <dd className="tabular font-semibold text-ink">{num(g.engine_contacts)}</dd>
                </div>
              </dl>
            </BentoCard>
          ))}
          <BentoCard span="full" icon={<TrendingUp />} title="Difference" description={`Since ${fmtDate(d.started)} (${d.days} days). Holdout ${d.enabled ? "on" : "now off"}, ${pct(d.share)} of synthetic patients.`}>
            {d.status === "ready" && d.difference ? (
              <p className="text-sm text-ink">
                Engine outreach group minus holdout, share at PDC 80%+:{" "}
                <span className="tabular font-semibold">
                  {d.difference.adherent_share > 0 ? "+" : ""}
                  {pct(d.difference.adherent_share, 1)}
                </span>{" "}
                <span className="text-ink-subtle">
                  (95% interval {pct(d.difference.ci[0], 1)} to {pct(d.difference.ci[1], 1)})
                </span>
                . An interval that includes zero means no difference can be claimed.
              </p>
            ) : (
              <p className="text-sm text-ink-muted">
                Not enough yet: the comparison needs at least {d.min_days} days and {d.min_n} therapies in each group.
              </p>
            )}
          </BentoCard>
        </BentoGrid>
      )}
    </>
  );
}
