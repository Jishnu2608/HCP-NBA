import { useQuery } from "@tanstack/react-query";
import { Database, ExternalLink } from "lucide-react";
import { api } from "../api";
import type { Json } from "../api";
import { Badge, Card, ErrorState, LoadingRows, PageHeader, Table, cx, fmtDate, num, titleCase } from "../ui";

const PIPELINE = [
  ["Unify", "One profile per HCP and patient from fills, interactions, consent and content."],
  ["Segment", "Adherence risk from days covered and gaps; HCP value and engagement tiers."],
  ["Predict", "Propensity models estimate response and refill for every candidate action."],
  ["Safeguard", "MLR status, consent and contact limits. Deterministic code, never a model."],
  ["Personalize", "Wording drafted from the approved content module, then validated."],
  ["Review", "A person approves, edits or rejects. Safeguards are checked again."],
  ["Engage", "Delivered through a channel adapter. Safeguards are checked a final time."],
  ["Capture and learn", "Responses and refills return as features; models retrain."],
];

const PRINCIPLES = [
  ["Safeguards are code", "Eligibility is decided by deterministic rules that run at generation, at approval and at send. No model or prompt can override them."],
  ["The model only drafts", "The language model receives an action that already passed every safeguard and may only word it. Its output is schema-checked and scanned before it is stored; failures fall back to templates."],
  ["Every decision is explained", "Each recommendation carries reasons traceable to data: risk drivers, response counts, safeguard results. The options that lost are kept too."],
  ["Access is enforced server-side", "Row-level rules live in the API. A role cannot fetch a record outside its scope, whatever the screen shows."],
  ["No look-ahead", "Model features are computed as they stood before each historical touch, by the same code used for live scoring."],
  ["Swappable seams", "Database, model provider, delivery channels and identity each sit behind one interface, so production systems replace demo ones without a rewrite."],
];

const MODEL_NOTE: Record<string, string> = {
  patient_engage: "Will this patient respond to this action on this channel?",
  patient_fill: "Will this outreach lead to a refill within days?",
  hcp_engage: "Will this HCP engage with this content on this channel?",
};

export default function UnderTheHood() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api("/meta") });
  const models = useQuery({ queryKey: ["models"], queryFn: () => api<Json[]>("/admin/models") });
  const active = (models.data ?? []).filter((m) => m.is_active);
  return (
    <>
      <PageHeader
        title="Under the hood"
        subtitle="How a recommendation is produced, what keeps it safe, and how well the models perform."
        action={
          <a
            href="/api/docs"
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-4 text-sm font-semibold text-ink shadow-card hover:bg-subtle"
          >
            Live API reference <ExternalLink className="h-4 w-4" aria-hidden />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        }
      />

      <Card title="The engagement loop" description="Step 8 feeds step 1: every response and refill becomes input to the next cycle.">
        <ol className="grid gap-x-6 gap-y-6 sm:grid-cols-2 xl:grid-cols-4">
          {PIPELINE.map(([name, text], i) => (
            <li key={name} className="relative border-t-2 border-line pt-4">
              <span
                aria-hidden
                className={cx("absolute -top-[2px] left-0 h-[2px] w-10", name === "Safeguard" ? "bg-accent" : "bg-primary")}
              />
              <div className="tabular text-xs font-semibold text-ink-subtle">{String(i + 1).padStart(2, "0")}</div>
              <div className="mt-0.5 text-[15px] font-semibold text-ink">{name}</div>
              <p className="mt-1 text-[13px] leading-5 text-ink-muted">{text}</p>
            </li>
          ))}
        </ol>
      </Card>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Card title="Design rules">
          <ul className="divide-y divide-line">
            {PRINCIPLES.map(([name, text]) => (
              <li key={name} className="py-3.5 first:pt-0 last:pb-0">
                <div className="text-sm font-semibold text-ink">{name}</div>
                <p className="mt-0.5 text-sm leading-6 text-ink-muted">{text}</p>
              </li>
            ))}
          </ul>
        </Card>

        <div className="min-w-0 space-y-6">
          <Card title="Propensity models" description="Held-out last 60 days. Higher AUC is better; 0.5 is chance.">
            {models.isLoading ? (
              <LoadingRows rows={3} label="Loading models" />
            ) : models.error ? (
              <ErrorState error={models.error} retry={() => void models.refetch()} title="Models could not be loaded" />
            ) : !active.length ? (
              <p className="text-sm text-ink-subtle">No trained models. The engine is using smoothed historical rates.</p>
            ) : (
              <>
                <Table
                  caption="Model performance"
                  head={["Model", "AUC", "No-model baseline", "Challenger", "Rows"]}
                  align={[undefined, "right", "right", "right", "right"]}
                >
                  {active.map((m) => (
                    <tr key={m.name}>
                      <td className="min-w-52">
                        <div className="font-semibold text-ink">
                          {titleCase(m.name)} <span className="text-xs font-normal text-ink-subtle">v{m.version}</span>
                        </div>
                        <div className="text-[13px] text-ink-subtle">{MODEL_NOTE[m.name]}</div>
                      </td>
                      <td className="tabular text-right font-semibold text-ink">{m.metrics.auc?.toFixed(3)}</td>
                      <td className="tabular text-right">{m.metrics.auc_baseline?.toFixed(3)}</td>
                      <td className="tabular text-right">{m.metrics.auc_challenger_gbm?.toFixed(3)}</td>
                      <td className="tabular text-right">{num(m.metrics.n_train + m.metrics.n_test)}</td>
                    </tr>
                  ))}
                </Table>
                <p className="mt-3 text-[13px] leading-5 text-ink-subtle">
                  Logistic regression is the champion because each prediction splits exactly into per-feature
                  contributions. The challenger is a boosted-tree model. Trained {fmtDate(active[0].as_of_date)} on
                  synthetic history; real-world performance will differ.
                </p>
              </>
            )}
          </Card>

          <Card
            title={
              <span className="flex items-center gap-2">
                <Database className="h-4 w-4 text-ink-subtle" aria-hidden /> Data in this environment
              </span>
            }
          >
            {meta.isLoading ? (
              <LoadingRows rows={4} label="Loading environment" />
            ) : meta.error ? (
              <ErrorState error={meta.error} retry={() => void meta.refetch()} />
            ) : (
              <>
                <div className="mb-4 flex flex-wrap gap-1.5">
                  <Badge tone="sage">Database: {meta.data.database}</Badge>
                  <Badge tone="sage">Drafting provider: {meta.data.llm_provider}</Badge>
                  <Badge tone="sage">Demo date: {fmtDate(meta.data.as_of_date)}</Badge>
                </div>
                <dl className="grid gap-x-6 sm:grid-cols-2">
                  {Object.entries(meta.data.row_counts as Record<string, number>)
                    .filter(([, n]) => n > 0)
                    .sort((a, b) => b[1] - a[1])
                    .map(([table, n]) => (
                      <div key={table} className="flex justify-between gap-3 border-b border-line py-2 text-sm">
                        <dt className="truncate text-ink-muted">{titleCase(table)}</dt>
                        <dd className="tabular font-semibold text-ink">{num(n)}</dd>
                      </div>
                    ))}
                </dl>
              </>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
