import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FastForward, Play, RotateCcw, Send } from "lucide-react";
import { useState } from "react";
import { api, post, put } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { Button, Card, ErrorNote, Loading, PageHeader, Table, fmtDate, fmtDateTime, titleCase } from "../ui";

const CONFIG_HELP: Record<string, string> = {
  pdc_window_days: "Rolling window, in days, for proportion of days covered.",
  pdc_threshold: "Days-covered level counted as adherent.",
  risk_weights: "Points each driver can add to the 0-100 adherence risk score.",
  risk_gap_days_cap: "Gap length, in days, at which the gap driver reaches full points.",
  risk_cutoffs: "Score at or above which a therapy is high or medium risk.",
  hcp_tiers: "Prescribing-volume percentile cut-offs for high and medium value.",
  frequency_caps: "Contact limits per target. A hard gate.",
  nba_due_soon_days: "How many days before run-out a proactive reminder may be raised.",
  nba_channel_cost: "Score points deducted for channels that use staff time.",
  nba_withheld_margin: "How much better a gated option must score to be reported as held back.",
  nba_hcp_min_score: "Minimum score for an HCP touch. Below this, nothing is recommended.",
  nba_repeat_content_days: "Content sent within this many days is down-weighted.",
};

function ConfigRow({ name, entry }: { name: string; entry: Json }) {
  const client = useQueryClient();
  const [text, setText] = useState(JSON.stringify(entry.value));
  const save = useMutation({
    mutationFn: () => put(`/admin/config/${name}`, { value: JSON.parse(text) }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["config"] }),
  });
  const changed = text !== JSON.stringify(entry.value);
  const isDefault = JSON.stringify(entry.value) === JSON.stringify(entry.default);
  let valid = true;
  try {
    JSON.parse(text);
  } catch {
    valid = false;
  }
  return (
    <tr>
      <td className="px-3 py-2.5 align-top">
        <div className="font-medium text-stone-800">{titleCase(name)}</div>
        <div className="text-xs text-stone-500">{CONFIG_HELP[name]}</div>
      </td>
      <td className="px-3 py-2.5 align-top">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="w-full min-w-72 rounded-lg border border-stone-300 px-2.5 py-1.5 font-mono text-xs"
        />
        {!isDefault && <div className="mt-1 text-xs text-amber-700">Changed from default {JSON.stringify(entry.default)}</div>}
        <ErrorNote error={save.error} />
      </td>
      <td className="px-3 py-2.5 align-top">
        <Button disabled={!changed || !valid} busy={save.isPending} onClick={() => save.mutate()}>
          Save
        </Button>
      </td>
    </tr>
  );
}

export default function Admin() {
  const client = useQueryClient();
  const { adopt } = useAuth();
  const config = useQuery({ queryKey: ["config"], queryFn: () => api("/admin/config") });
  const cycles = useQuery({ queryKey: ["cycles"], queryFn: () => api<Json[]>("/admin/cycles") });
  const [result, setResult] = useState<Json | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const run = useMutation({
    mutationFn: (fn: () => Promise<Json>) => fn(),
    onSuccess: (data) => {
      const { access_token, user, ...shown } = data;
      if (access_token) adopt({ access_token, user });
      setResult(shown);
      setConfirmReset(false);
      void client.invalidateQueries();
    },
  });

  return (
    <>
      <PageHeader
        title="Engine"
        subtitle="Run a recommendation cycle, move the demo date forward so responses and refills land, or restore the seeded starting point."
      />
      <div className="grid gap-5 xl:grid-cols-3">
        <Card title="Run a cycle">
          <p className="mb-3 text-sm text-stone-600">
            Recalculates adherence and segments, regenerates recommendations and drafts for the current
            demo date. Use after approving content or changing settings.
          </p>
          <Button variant="primary" busy={run.isPending} onClick={() => run.mutate(() => post("/admin/cycle"))}>
            <Play className="h-4 w-4" /> Run cycle now
          </Button>
          <p className="mb-3 mt-5 border-t border-stone-100 pt-4 text-sm text-stone-600">
            Demo shortcut: approve and send the top ready recommendations at once, as a team working
            its queues would over a week. Each still passes the gate re-check and is audited.
          </p>
          <Button busy={run.isPending} onClick={() => run.mutate(() => post("/admin/bulk-send", { limit: 400 }))}>
            <Send className="h-4 w-4" /> Send top 400
          </Button>
        </Card>
        <Card title="Advance the demo clock">
          <p className="mb-3 text-sm text-stone-600">
            Time passes: sent messages get their responses, patients refill (or do not), models retrain
            on the new outcomes, and a fresh cycle runs.
          </p>
          <div className="flex flex-wrap gap-2">
            {[1, 7, 14].map((days) => (
              <Button
                key={days}
                busy={run.isPending}
                onClick={() => run.mutate(() => post("/admin/advance", { days, retrain: true }))}
              >
                <FastForward className="h-4 w-4" /> {days} {days === 1 ? "day" : "days"}
              </Button>
            ))}
          </div>
        </Card>
        <Card title="Reset the demo">
          <p className="mb-3 text-sm text-stone-600">
            Rebuilds the synthetic dataset at its starting date. Registered accounts and their
            assignments are kept. Takes about half a minute.
          </p>
          {confirmReset ? (
            <div className="flex gap-2">
              <Button variant="danger" busy={run.isPending} onClick={() => run.mutate(() => post("/admin/reset"))}>
                Yes, erase and rebuild
              </Button>
              <Button variant="ghost" onClick={() => setConfirmReset(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button variant="danger" onClick={() => setConfirmReset(true)}>
              <RotateCcw className="h-4 w-4" /> Reset to seeded data
            </Button>
          )}
        </Card>
      </div>

      <div className="mt-4">
        <ErrorNote error={run.error} />
      </div>
      {result && (
        <Card title="Last operation" className="mt-4">
          <pre className="max-h-64 overflow-auto rounded-lg bg-stone-50 p-3 text-xs text-stone-700">
            {JSON.stringify(result, null, 2)}
          </pre>
        </Card>
      )}

      <Card title="Engine settings" className="mt-5">
        {config.isLoading ? (
          <Loading />
        ) : config.error ? (
          <ErrorNote error={config.error} />
        ) : (
          <Table head={["Setting", "Value (JSON)", ""]}>
            {Object.entries(config.data as Record<string, Json>).map(([name, entry]) => (
              <ConfigRow key={`${name}-${JSON.stringify(entry.value)}`} name={name} entry={entry} />
            ))}
          </Table>
        )}
        <p className="mt-3 text-xs text-stone-500">
          Every change is written to the audit log and takes effect at the next cycle.
        </p>
      </Card>

      <Card title="Recent cycles" className="mt-5">
        {cycles.isLoading ? (
          <Loading />
        ) : (
          <Table head={["Cycle", "Demo date", "Run at", "Patient ready", "Patient blocked", "HCP ready", "Held back", "Superseded"]}>
            {(cycles.data ?? []).map((c) => (
              <tr key={c.id}>
                <td className="tabular px-3 py-2">{c.id}</td>
                <td className="px-3 py-2">{fmtDate(c.as_of_date)}</td>
                <td className="px-3 py-2 text-stone-500">{fmtDateTime(`${c.started_ts}Z`)}</td>
                <td className="tabular px-3 py-2">{c.stats?.patient_ready ?? 0}</td>
                <td className="tabular px-3 py-2">{c.stats?.patient_blocked ?? 0}</td>
                <td className="tabular px-3 py-2">{c.stats?.hcp_ready ?? 0}</td>
                <td className="tabular px-3 py-2">
                  {(c.stats?.patient_withheld ?? 0) + (c.stats?.hcp_withheld ?? 0)}
                </td>
                <td className="tabular px-3 py-2">{c.stats?.expired_superseded ?? 0}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
