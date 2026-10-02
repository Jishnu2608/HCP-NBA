import { useQuery } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, query } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { P } from "../permissions";
import {
  Badge,
  Card,
  Empty,
  ErrorNote,
  Loading,
  PageHeader,
  SegmentBadge,
  StatusBadge,
  Table,
  cx,
} from "../ui";

const PAGE = 25;
const TITLE: Record<string, [string, string]> = {
  patients: [
    "Adherence queue",
    "Patients who need outreach now, ranked by risk and the chance the action leads to a fill.",
  ],
  hcps: [
    "HCP queue",
    "Next best action for each assigned HCP, ranked by value and predicted engagement.",
  ],
  gated: [
    "Gate outcomes",
    "Recommendations that were blocked, or where a better option was held back by a gate. Identities are hidden.",
  ],
  all: ["All recommendations", "Every open recommendation across both audiences."],
};

const FILTERS: Array<{ key: string; label: string; statuses: string[] }> = [
  { key: "open", label: "Open", statuses: ["ready_for_review", "approved", "blocked"] },
  { key: "ready_for_review", label: "Ready", statuses: ["ready_for_review"] },
  { key: "approved", label: "Approved", statuses: ["approved"] },
  { key: "sent", label: "Sent", statuses: ["sent", "responded"] },
  { key: "blocked", label: "Blocked", statuses: ["blocked"] },
  { key: "closed", label: "Rejected / superseded", statuses: ["rejected", "expired"] },
];

export default function Queue() {
  const { can } = useAuth();
  const [filter, setFilter] = useState("open");
  const [audience, setAudience] = useState("");
  const [page, setPage] = useState(0);
  const statuses = FILTERS.find((f) => f.key === filter)!.statuses;
  const list = useQuery({
    queryKey: ["nba", filter, audience, page],
    queryFn: () =>
      api(
        `/nba${query({ status: statuses, target_type: audience, limit: PAGE, offset: page * PAGE })}`,
      ),
  });
  const seesAll = can(P.NBA_READ_ALL);
  const view = seesAll
    ? "all"
    : can(P.NBA_READ_GATED)
      ? "gated"
      : can(P.NBA_READ_HCP_ASSIGNED)
        ? "hcps"
        : "patients";
  const [title, subtitle] = TITLE[view];
  const counts: Record<string, number> = list.data?.counts ?? {};
  const countFor = (keys: string[]) => keys.reduce((n, k) => n + (counts[k] ?? 0), 0);

  return (
    <>
      <PageHeader title={title} subtitle={subtitle} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => {
              setFilter(f.key);
              setPage(0);
            }}
            className={cx(
              "rounded-full px-3 py-1.5 text-sm font-medium",
              filter === f.key
                ? "bg-brand-600 text-white"
                : "bg-white text-stone-600 ring-1 ring-inset ring-stone-200 hover:bg-stone-50",
            )}
          >
            {f.label}
            <span className="tabular ml-1.5 opacity-70">{countFor(f.statuses)}</span>
          </button>
        ))}
        {seesAll && (
          <select
            value={audience}
            onChange={(e) => {
              setAudience(e.target.value);
              setPage(0);
            }}
            className="ml-auto rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-sm"
          >
            <option value="">Both audiences</option>
            <option value="PATIENT">Patients</option>
            <option value="HCP">HCPs</option>
          </select>
        )}
      </div>

      <Card>
        {list.isLoading ? (
          <Loading />
        ) : list.error ? (
          <ErrorNote error={list.error} />
        ) : !list.data.items.length ? (
          <Empty>Nothing in this view.</Empty>
        ) : (
          <>
            <Table head={["Who", "Recommended action", "Why", "Priority", "Status"]}>
              {list.data.items.map((n: Json) => (
                <tr key={n.id} className="hover:bg-stone-50">
                  <td className="px-3 py-3 align-top">
                    <Link to={`/nba/${n.id}`} className="font-medium text-brand-700 hover:underline">
                      {n.target_name ?? `${n.target_type === "HCP" ? "HCP" : "Patient"} (identity hidden)`}
                    </Link>
                    <div className="mt-1 flex items-center gap-1.5">
                      {n.target_name && <span className="text-xs text-stone-400">{n.target_id}</span>}
                      <SegmentBadge value={n.segment} />
                    </div>
                  </td>
                  <td className="px-3 py-3 align-top">
                    <div className="font-medium text-stone-800">
                      {capitalize(n.action_label)} by {n.channel_label}
                    </div>
                    <div className="mt-0.5 text-xs text-stone-500">{n.content_title}</div>
                  </td>
                  <td className="max-w-md px-3 py-3 align-top text-stone-600">
                    {n.status === "blocked" ? (
                      <span className="flex items-start gap-1.5 text-red-700">
                        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
                        {n.block_reason}
                      </span>
                    ) : (
                      (n.rationale_summary ?? n.timing_note)
                    )}
                    {n.has_withheld && n.status !== "blocked" && (
                      <div className="mt-1">
                        <Badge tone="warn">Better option held back by a gate</Badge>
                      </div>
                    )}
                  </td>
                  <td className="tabular px-3 py-3 align-top text-stone-700">{n.priority.toFixed(1)}</td>
                  <td className="px-3 py-3 align-top">
                    <StatusBadge status={n.status} />
                  </td>
                </tr>
              ))}
            </Table>
            <div className="mt-4 flex items-center justify-between text-sm text-stone-500">
              <span className="tabular">
                {page * PAGE + 1}–{Math.min((page + 1) * PAGE, list.data.total)} of {list.data.total}
              </span>
              <div className="flex gap-2">
                <button
                  disabled={page === 0}
                  onClick={() => setPage(page - 1)}
                  className="rounded-lg border border-stone-300 px-3 py-1 disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  disabled={(page + 1) * PAGE >= list.data.total}
                  onClick={() => setPage(page + 1)}
                  className="rounded-lg border border-stone-300 px-3 py-1 disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </Card>
    </>
  );
}

function capitalize(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
