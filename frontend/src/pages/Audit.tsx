import { useQuery } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, query } from "../api";
import type { Json } from "../api";
import { Card, ErrorNote, Loading, PageHeader, Table, eventLabel, fmtDateTime, titleCase } from "../ui";

function Flag({ value }: { value: boolean | null }) {
  if (value === null) return <span className="text-stone-300">—</span>;
  return value ? (
    <Check className="h-4 w-4 text-emerald-600" aria-label="passed" />
  ) : (
    <X className="h-4 w-4 text-red-600" aria-label="failed" />
  );
}

export default function Audit() {
  const [action, setAction] = useState("");
  const [page, setPage] = useState(0);
  const PAGE = 50;
  const log = useQuery({
    queryKey: ["audit", action, page],
    queryFn: () => api(`/audit${query({ action, limit: PAGE, offset: page * PAGE })}`),
  });
  const actions: Record<string, number> = log.data?.actions ?? {};
  return (
    <>
      <PageHeader
        title="Audit log"
        subtitle="Append-only record of every recommendation, gate result, review decision, send, response and configuration change, with who did it."
        action={
          <select
            value={action}
            onChange={(e) => {
              setAction(e.target.value);
              setPage(0);
            }}
            className="rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-sm"
          >
            <option value="">All events</option>
            {Object.entries(actions)
              .sort()
              .map(([name, count]) => (
                <option key={name} value={name}>
                  {eventLabel(name)} ({count})
                </option>
              ))}
          </select>
        }
      />
      <Card>
        {log.isLoading ? (
          <Loading />
        ) : log.error ? (
          <ErrorNote error={log.error} />
        ) : (
          <>
            <Table head={["When", "Event", "Actor", "Subject", "MLR", "Consent", "Reason"]}>
              {log.data.items.map((a: Json) => (
                <tr key={a.id}>
                  <td className="whitespace-nowrap px-3 py-2 text-stone-500">{fmtDateTime(a.ts)}</td>
                  <td className="px-3 py-2 font-medium text-stone-800">{eventLabel(a.action)}</td>
                  <td className="px-3 py-2 text-stone-600">
                    {a.actor} <span className="text-xs text-stone-400">{titleCase(a.actor_role)}</span>
                  </td>
                  <td className="px-3 py-2 text-stone-600">
                    {a.nba_id ? (
                      <Link to={`/nba/${a.nba_id}`} className="text-brand-700 hover:underline">
                        Recommendation {a.nba_id}
                      </Link>
                    ) : (
                      `${titleCase(a.entity_type)} ${a.entity_id}`
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <Flag value={a.compliance_ok} />
                  </td>
                  <td className="px-3 py-2">
                    <Flag value={a.consent_ok} />
                  </td>
                  <td className="max-w-sm px-3 py-2 text-stone-600">{a.reason ?? ""}</td>
                </tr>
              ))}
            </Table>
            <div className="mt-4 flex items-center justify-between text-sm text-stone-500">
              <span className="tabular">{log.data.total.toLocaleString()} events</span>
              <div className="flex gap-2">
                <button
                  disabled={page === 0}
                  onClick={() => setPage(page - 1)}
                  className="rounded-lg border border-stone-300 px-3 py-1 disabled:opacity-40"
                >
                  Newer
                </button>
                <button
                  disabled={(page + 1) * PAGE >= log.data.total}
                  onClick={() => setPage(page + 1)}
                  className="rounded-lg border border-stone-300 px-3 py-1 disabled:opacity-40"
                >
                  Older
                </button>
              </div>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
