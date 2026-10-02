import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { api } from "../api";
import type { Json } from "../api";
import { Badge, Card, ErrorNote, Loading, PageHeader, Table, fmtDate, titleCase } from "../ui";

const PIPELINE = [
  ["Unify", "One profile per HCP and patient from fills, interactions, consent and content."],
  ["Segment", "Adherence risk from days covered and gaps; HCP value and engagement tiers."],
  ["Predict", "Propensity models estimate response and fill for every candidate action."],
  ["Gate", "MLR status, consent and contact limits. Deterministic code, never a model."],
  ["Personalise", "Wording drafted from the approved content module, then validated."],
  ["Review", "A person approves, edits or rejects. Gates are checked again."],
  ["Engage", "Delivered through a channel adapter. Gates are checked a final time."],
  ["Capture and learn", "Responses and refills return as features; models retrain."],
];

const PRINCIPLES = [
  ["Gates are code", "Eligibility is decided by deterministic rules that run at generation, at approval and at send. No model or prompt can override them."],
  ["The model only drafts", "The language model receives an action that already passed every gate and may only word it. Its output is schema-checked and scanned before it is stored; failures fall back to templates."],
  ["Every decision is explained", "Each recommendation carries reasons traceable to data: risk drivers, response counts, gate results. The options that lost are kept too."],
  ["Access is enforced server-side", "Row-level rules live in the API. A role cannot fetch a record outside its scope, whatever the screen shows."],
  ["No look-ahead", "Model features are computed as they stood before each historical touch, by the same code used for live scoring."],
  ["Swappable seams", "Database, model provider, delivery channels and identity each sit behind one interface, so production systems replace demo ones without a rewrite."],
];

const MODEL_NOTE: Record<string, string> = {
  patient_engage: "Will this patient respond to this action on this channel?",
  patient_fill: "Will this outreach lead to a fill within days?",
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
            className="inline-flex items-center gap-1.5 rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm font-medium text-stone-700 hover:bg-stone-50"
          >
            Live API reference <ExternalLink className="h-4 w-4" />
          </a>
        }
      />

      <Card title="The engagement loop">
        <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {PIPELINE.map(([name, text], i) => (
            <li key={name} className="rounded-lg border border-stone-200 bg-stone-50 p-3">
              <div className="text-xs font-semibold text-brand-700">
                {i + 1}. {name}
              </div>
              <div className="mt-1 text-xs leading-snug text-stone-600">{text}</div>
            </li>
          ))}
        </ol>
        <p className="mt-3 text-xs text-stone-500">
          Step 8 feeds step 1: every response and refill becomes input to the next cycle.
        </p>
      </Card>

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <Card title="Design rules">
          <ul className="space-y-3">
            {PRINCIPLES.map(([name, text]) => (
              <li key={name}>
                <div className="text-sm font-semibold text-stone-900">{name}</div>
                <div className="text-sm text-stone-600">{text}</div>
              </li>
            ))}
          </ul>
        </Card>

        <div className="space-y-5">
          <Card title="Propensity models (held-out last 60 days)">
            {models.isLoading ? (
              <Loading />
            ) : models.error ? (
              <ErrorNote error={models.error} />
            ) : !active.length ? (
              <p className="text-sm text-stone-500">No trained models. The engine is using smoothed historical rates.</p>
            ) : (
              <>
                <Table head={["Model", "AUC", "No-model baseline", "Boosted-tree challenger", "Rows"]}>
                  {active.map((m) => (
                    <tr key={m.name}>
                      <td className="px-3 py-2">
                        <div className="font-medium text-stone-800">
                          {titleCase(m.name)} <span className="text-xs text-stone-400">v{m.version}</span>
                        </div>
                        <div className="text-xs text-stone-500">{MODEL_NOTE[m.name]}</div>
                      </td>
                      <td className="tabular px-3 py-2 font-semibold">{m.metrics.auc?.toFixed(3)}</td>
                      <td className="tabular px-3 py-2">{m.metrics.auc_baseline?.toFixed(3)}</td>
                      <td className="tabular px-3 py-2">{m.metrics.auc_challenger_gbm?.toFixed(3)}</td>
                      <td className="tabular px-3 py-2">
                        {(m.metrics.n_train + m.metrics.n_test).toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </Table>
                <p className="mt-3 text-xs text-stone-500">
                  Logistic regression is the champion because each prediction splits exactly into per-feature
                  contributions. Trained {fmtDate(active[0].as_of_date)} on synthetic history; real-world
                  performance will differ.
                </p>
              </>
            )}
          </Card>

          <Card title="Data in this environment">
            {meta.isLoading ? (
              <Loading />
            ) : (
              <>
                <div className="mb-3 flex flex-wrap gap-1.5">
                  <Badge>Database: {meta.data.database}</Badge>
                  <Badge>Drafting provider: {meta.data.llm_provider}</Badge>
                  <Badge>Demo date: {fmtDate(meta.data.as_of_date)}</Badge>
                </div>
                <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
                  {Object.entries(meta.data.row_counts as Record<string, number>)
                    .filter(([, n]) => n > 0)
                    .sort((a, b) => b[1] - a[1])
                    .map(([table, n]) => (
                      <div key={table} className="flex justify-between gap-2 border-b border-stone-100 py-1">
                        <span className="text-stone-600">{titleCase(table)}</span>
                        <span className="tabular text-stone-900">{n.toLocaleString()}</span>
                      </div>
                    ))}
                </div>
              </>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
