import { useQuery } from "@tanstack/react-query";
import { Check, Minus, ScrollText, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, query } from "../api";
import type { Json } from "../api";
import {
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingRows,
  PageHeader,
  Pagination,
  Select,
  Toolbar,
  cx,
  eventLabel,
  fmtDateTime,
  num,
  titleCase,
} from "../ui";
import type { Column } from "../ui";

/** Safeguard result: icon and word, never colour alone. */
function Flag({ value, label }: { value: boolean | null; label: string }) {
  if (value === null)
    return (
      <span className="inline-flex items-center gap-1 text-ink-subtle" title={`${label}: not applicable`}>
        <Minus className="h-3.5 w-3.5" aria-hidden />
        <span className="sr-only">Not applicable</span>
      </span>
    );
  return value ? (
    <span className="inline-flex items-center gap-1 text-[13px] font-medium text-ok">
      <Check className="h-3.5 w-3.5" aria-hidden /> Pass
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-[13px] font-medium text-bad">
      <X className="h-3.5 w-3.5" aria-hidden /> Fail
    </span>
  );
}

const PAGE = 50;

export default function Audit() {
  const [action, setAction] = useState("");
  const [page, setPage] = useState(0);
  const log = useQuery({
    queryKey: ["audit", action, page],
    queryFn: () => api(`/audit${query({ action, limit: PAGE, offset: page * PAGE })}`),
    placeholderData: (previous) => previous,
  });
  const actions: Record<string, number> = log.data?.actions ?? {};

  const columns: Column<Json>[] = [
    {
      key: "event",
      header: "Event",
      primary: true,
      className: "min-w-48",
      cell: (a) => (
        <div>
          <div className={cx("font-semibold", /block|reject/.test(a.action) ? "text-bad" : "text-ink")}>{eventLabel(a.action)}</div>
          <div className="tabular text-[13px] text-ink-subtle @4xl:hidden">{fmtDateTime(a.ts)}</div>
        </div>
      ),
    },
    {
      key: "when",
      header: "When",
      hideOnMobile: true,
      className: "whitespace-nowrap",
      cell: (a) => <span className="tabular text-ink-muted">{fmtDateTime(a.ts)}</span>,
    },
    {
      key: "actor",
      header: "Actor",
      cell: (a) => (
        <div className="min-w-0">
          <div className="max-w-56 truncate text-ink" title={a.actor}>
            {a.actor}
          </div>
          <div className="text-xs text-ink-subtle">{titleCase(a.actor_role)}</div>
        </div>
      ),
    },
    {
      key: "subject",
      header: "Subject",
      cell: (a) =>
        a.nba_id ? (
          <Link
            to={`/nba/${a.nba_id}`}
            onClick={(e) => e.stopPropagation()}
            className="font-medium text-primary-ink hover:underline"
          >
            Recommendation {a.nba_id}
          </Link>
        ) : (
          <span className="text-ink-muted">
            {titleCase(a.entity_type)} <span className="tabular">{a.entity_id}</span>
          </span>
        ),
    },
    { key: "mlr", header: "MLR", cell: (a) => <Flag value={a.compliance_ok} label="MLR" /> },
    { key: "consent", header: "Consent", cell: (a) => <Flag value={a.consent_ok} label="Consent" /> },
    {
      key: "reason",
      header: "Reason",
      className: "min-w-56",
      cell: (a) =>
        a.reason ? (
          <span className="line-clamp-2 text-[13px] text-ink-muted" title={a.reason}>
            {a.reason}
          </span>
        ) : (
          <span className="text-ink-subtle">—</span>
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Audit log"
        subtitle="Append-only record of every recommendation, safeguard result, review decision, send, response and configuration change, with who did it."
      />
      <Toolbar>
        <p className="text-sm text-ink-subtle">{log.data ? `${num(log.data.total)} events` : " "}</p>
        <Select
          label="Event type"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setPage(0);
          }}
          className="w-full lg:w-80"
        >
          <option value="">All events</option>
          {Object.entries(actions)
            .sort()
            .map(([name, count]) => (
              <option key={name} value={name}>
                {eventLabel(name)} ({num(count)})
              </option>
            ))}
        </Select>
      </Toolbar>
      <Card flush>
        {log.isLoading ? (
          <div className="p-5">
            <LoadingRows rows={10} label="Loading audit events" />
          </div>
        ) : log.error ? (
          <ErrorState error={log.error} title="The audit log could not be loaded" />
        ) : !log.data.items.length ? (
          <EmptyState title="No events of this type" icon={<ScrollText className="h-5 w-5" />} />
        ) : (
          <>
            <DataTable caption="Audit events" tableFrom="4xl" columns={columns} rows={log.data.items} rowKey={(a) => a.id} />
            <Pagination page={page} pageSize={PAGE} total={log.data.total} onPage={setPage} previousLabel="Newer" nextLabel="Older" />
          </>
        )}
      </Card>
    </>
  );
}
