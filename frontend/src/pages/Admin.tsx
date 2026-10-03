import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FastForward, History, Play, RotateCcw, Send, Settings2, TriangleAlert } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import { api, post, put } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { useToast } from "../toast";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  ErrorNote,
  ErrorState,
  LoadingRows,
  PageHeader,
  cx,
  fmtDate,
  fmtDateTime,
  num,
  titleCase,
} from "../ui";
import type { Column } from "../ui";

const CONFIG_HELP: Record<string, string> = {
  pdc_window_days: "Rolling window, in days, for proportion of days covered.",
  pdc_threshold: "Days-covered level counted as adherent.",
  risk_weights: "Points each driver can add to the 0-100 adherence risk score.",
  risk_gap_days_cap: "Gap length, in days, at which the gap driver reaches full points.",
  risk_cutoffs: "Score at or above which a therapy is high or medium risk.",
  hcp_tiers: "Prescribing-volume percentile cut-offs for high and medium value.",
  frequency_caps: "Contact limits per target. A hard safeguard.",
  nba_due_soon_days: "How many days before run-out a proactive reminder may be raised.",
  nba_channel_cost: "Score points deducted for channels that use staff time.",
  nba_withheld_margin: "How much better a blocked option must score to be reported as held back.",
  nba_hcp_min_score: "Minimum score for an HCP touch. Below this, nothing is recommended.",
  nba_repeat_content_days: "Content sent within this many days is down-weighted.",
};

function ConfigRow({ name, entry }: { name: string; entry: Json }) {
  const client = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState(JSON.stringify(entry.value));
  const save = useMutation({
    mutationFn: () => put(`/admin/config/${name}`, { value: JSON.parse(text) }),
    onSuccess: () => {
      toast(`${titleCase(name)} saved. It applies from the next cycle.`);
      void client.invalidateQueries({ queryKey: ["config"] });
    },
  });
  const changed = text !== JSON.stringify(entry.value);
  const isDefault = JSON.stringify(entry.value) === JSON.stringify(entry.default);
  let valid = true;
  try {
    JSON.parse(text);
  } catch {
    valid = false;
  }
  const inputId = `config-${name}`;
  return (
    <li className="grid gap-3 px-5 py-4 sm:px-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_auto] lg:items-start lg:gap-6">
      <div className="min-w-0">
        <label htmlFor={inputId} className="text-sm font-semibold text-ink">
          {titleCase(name)}
        </label>
        <p className="mt-0.5 text-[13px] leading-5 text-ink-subtle">{CONFIG_HELP[name]}</p>
      </div>
      <div className="min-w-0">
        <input
          id={inputId}
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          aria-invalid={!valid || undefined}
          className={cx(
            "block h-10 w-full rounded-lg border bg-surface px-3 font-mono text-[13px] text-ink shadow-card",
            "focus:border-primary focus:outline-none focus:ring-[3px] focus:ring-primary/20",
            valid ? "border-line-strong" : "border-bad",
          )}
        />
        {!valid && <p className="mt-1 text-[13px] text-bad">Not valid JSON.</p>}
        {!isDefault && (
          <p className="mt-1 break-all text-[13px] text-warn">Changed from default {JSON.stringify(entry.default)}</p>
        )}
        <ErrorNote error={save.error} className="mt-2" />
      </div>
      <div className="flex gap-2 lg:justify-end">
        <Button disabled={!changed || !valid} busy={save.isPending} onClick={() => save.mutate()}>
          Save
        </Button>
        {changed && (
          <Button variant="ghost" onClick={() => setText(JSON.stringify(entry.value))}>
            Undo
          </Button>
        )}
      </div>
    </li>
  );
}

function Operation({
  icon,
  title,
  children,
  actions,
  tone = "default",
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
  actions: ReactNode;
  tone?: "default" | "danger";
}) {
  return (
    <section
      className={cx(
        "flex min-w-0 flex-col rounded-xl border bg-surface p-5 shadow-card sm:p-6",
        tone === "danger" ? "border-bad-line" : "border-line",
      )}
    >
      <span
        className={cx(
          "grid h-10 w-10 place-items-center rounded-[10px]",
          tone === "danger" ? "bg-bad-soft text-bad" : "bg-primary-soft text-primary-ink",
        )}
        aria-hidden
      >
        {icon}
      </span>
      <h2 className="mt-4 text-[15px] font-semibold text-ink">{title}</h2>
      <div className="mt-1.5 flex-1 text-sm leading-6 text-ink-muted">{children}</div>
      <div className="mt-5 flex flex-wrap gap-2">{actions}</div>
    </section>
  );
}

/** A finished operation's result as readable facts rather than raw JSON. */
function Result({ data }: { data: Json }) {
  const flat = Object.entries(data).filter(([, v]) => typeof v !== "object" || v === null);
  const nested = Object.entries(data).filter(([, v]) => typeof v === "object" && v !== null);
  return (
    <div>
      {flat.length > 0 && (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-4">
          {flat.map(([k, v]) => (
            <div key={k} className="min-w-0">
              <dt className="truncate text-[13px] text-ink-subtle">{titleCase(k)}</dt>
              <dd className="tabular truncate text-sm font-semibold text-ink">{typeof v === "number" ? num(v, 2).replace(/\.00$/, "") : String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {nested.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-[13px] font-semibold text-primary-ink">Full details</summary>
          <pre className="scroll-quiet mt-2 max-h-72 overflow-auto rounded-lg bg-subtle p-3 text-xs text-ink-muted">
            {JSON.stringify(Object.fromEntries(nested), null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

export default function Admin() {
  const client = useQueryClient();
  const toast = useToast();
  const { adopt } = useAuth();
  const config = useQuery({ queryKey: ["config"], queryFn: () => api("/admin/config") });
  const cycles = useQuery({ queryKey: ["cycles"], queryFn: () => api<Json[]>("/admin/cycles") });
  const [result, setResult] = useState<{ label: string; data: Json } | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const run = useMutation({
    mutationFn: (v: { fn: () => Promise<Json>; label: string }) => v.fn(),
    onSuccess: (data, v) => {
      const { access_token, user, ...shown } = data;
      if (access_token) adopt({ access_token, user });
      setResult({ label: v.label, data: shown });
      setConfirmReset(false);
      toast(`${v.label}: done`);
      void client.invalidateQueries();
    },
  });
  const busy = run.isPending;
  const running = (label: string) => busy && run.variables?.label === label;

  const cycleColumns: Column<Json>[] = [
    {
      key: "cycle",
      header: "Cycle",
      primary: true,
      cell: (c) => (
        <span className="font-semibold text-ink">
          Cycle <span className="tabular">{c.id}</span>
        </span>
      ),
    },
    { key: "date", header: "Demo date", cell: (c) => <span className="tabular">{fmtDate(c.as_of_date)}</span> },
    { key: "run", header: "Run at", cell: (c) => <span className="tabular text-ink-subtle">{fmtDateTime(`${c.started_ts}Z`)}</span> },
    { key: "pr", header: "Patient ready", align: "right", cell: (c) => <span className="tabular">{num(c.stats?.patient_ready ?? 0)}</span> },
    { key: "pb", header: "Patient blocked", align: "right", cell: (c) => <span className="tabular">{num(c.stats?.patient_blocked ?? 0)}</span> },
    { key: "hr", header: "HCP ready", align: "right", cell: (c) => <span className="tabular">{num(c.stats?.hcp_ready ?? 0)}</span> },
    {
      key: "held",
      header: "Held back",
      align: "right",
      cell: (c) => <span className="tabular">{num((c.stats?.patient_withheld ?? 0) + (c.stats?.hcp_withheld ?? 0))}</span>,
    },
    { key: "sup", header: "Superseded", align: "right", cell: (c) => <span className="tabular">{num(c.stats?.expired_superseded ?? 0)}</span> },
  ];

  return (
    <>
      <PageHeader
        title="Engine"
        subtitle="Run a recommendation cycle, move the demo date forward so responses and refills land, or restore the seeded starting point."
      />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Operation
          icon={<Play className="h-5 w-5" />}
          title="Run a cycle"
          actions={
            <>
              <Button variant="primary" busy={running("Cycle")} disabled={busy} onClick={() => run.mutate({ fn: () => post("/admin/cycle"), label: "Cycle" })}>
                <Play className="h-4 w-4" aria-hidden /> Run cycle now
              </Button>
              <Button
                busy={running("Bulk send")}
                disabled={busy}
                onClick={() => run.mutate({ fn: () => post("/admin/bulk-send", { limit: 400 }), label: "Bulk send" })}
              >
                <Send className="h-4 w-4" aria-hidden /> Send top 400
              </Button>
            </>
          }
        >
          Recalculates adherence and segments, then regenerates recommendations and drafts for the current demo date.
          <span className="mt-2 block text-[13px] text-ink-subtle">
            Send top 400 is a demo shortcut: each message still passes the safeguard re-check and is audited.
          </span>
        </Operation>

        <Operation
          icon={<FastForward className="h-5 w-5" />}
          title="Advance the demo clock"
          actions={[1, 7, 14].map((days) => (
            <Button
              key={days}
              busy={running(`Advance ${days}`)}
              disabled={busy}
              onClick={() => run.mutate({ fn: () => post("/admin/advance", { days, retrain: true }), label: `Advance ${days}` })}
            >
              <FastForward className="h-4 w-4" aria-hidden /> {days} {days === 1 ? "day" : "days"}
            </Button>
          ))}
        >
          Time passes: sent messages get their responses, patients refill or do not, models retrain on the new outcomes,
          and a fresh cycle runs.
        </Operation>

        <Operation
          tone="danger"
          icon={<RotateCcw className="h-5 w-5" />}
          title="Reset the demo"
          actions={
            confirmReset ? (
              <>
                <Button variant="danger" busy={running("Reset")} onClick={() => run.mutate({ fn: () => post("/admin/reset"), label: "Reset" })}>
                  Yes, erase and rebuild
                </Button>
                <Button variant="ghost" disabled={busy} onClick={() => setConfirmReset(false)}>
                  Cancel
                </Button>
              </>
            ) : (
              <Button variant="quiet-danger" disabled={busy} onClick={() => setConfirmReset(true)}>
                <RotateCcw className="h-4 w-4" aria-hidden /> Reset to seeded data
              </Button>
            )
          }
        >
          Rebuilds the synthetic dataset at its starting date. Registered accounts and their assignments are kept.
          {confirmReset && (
            <span className="mt-2 flex items-start gap-1.5 text-[13px] font-medium text-bad">
              <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /> All recommendations, messages and
              outcomes since the start are erased.
            </span>
          )}
        </Operation>
      </div>

      {busy && (
        <Alert tone="info" className="mt-6" title="Working">
          <span className="text-info">This can take up to half a minute at full scale. Keep this page open.</span>
        </Alert>
      )}
      <ErrorNote error={run.error} className="mt-6" />
      {result && !busy && (
        <Card title={`Last operation: ${result.label}`} className="mt-6" action={<Badge tone="ok">Completed</Badge>}>
          <Result data={result.data} />
        </Card>
      )}

      <Card
        flush
        className="mt-6"
        title={
          <span className="flex items-center gap-2">
            <Settings2 className="h-4 w-4 text-ink-subtle" aria-hidden /> Engine settings
          </span>
        }
        description="Every change is written to the audit log and takes effect at the next cycle. Values are JSON."
      >
        {config.isLoading ? (
          <div className="p-5">
            <LoadingRows rows={6} label="Loading settings" />
          </div>
        ) : config.error ? (
          <ErrorState error={config.error} retry={() => void config.refetch()} title="Settings could not be loaded" />
        ) : (
          <ul className="divide-y divide-line border-t border-line">
            {Object.entries(config.data as Record<string, Json>).map(([name, entry]) => (
              <ConfigRow key={`${name}-${JSON.stringify(entry.value)}`} name={name} entry={entry} />
            ))}
          </ul>
        )}
      </Card>

      <Card
        flush
        className="mt-6"
        title={
          <span className="flex items-center gap-2">
            <History className="h-4 w-4 text-ink-subtle" aria-hidden /> Recent cycles
          </span>
        }
      >
        {cycles.isLoading ? (
          <div className="p-5">
            <LoadingRows rows={4} label="Loading cycles" />
          </div>
        ) : cycles.error ? (
          <ErrorState error={cycles.error} retry={() => void cycles.refetch()} title="Cycles could not be loaded" />
        ) : (
          <div className="border-t border-line">
            <DataTable caption="Recent cycles" columns={cycleColumns} rows={cycles.data ?? []} rowKey={(c) => c.id} />
          </div>
        )}
      </Card>
    </>
  );
}
