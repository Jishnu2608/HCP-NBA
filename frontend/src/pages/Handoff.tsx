// The governed hand-off to a CRM or marketing-automation system (GET /api/integration/crm/*).
// Read-only: approved recommendations in the reader's own scope, with delivery and outcome,
// under a fixed field map. Every preview and download is audited on the server.
import { useQuery } from "@tanstack/react-query";
import { Download, FileJson, ShieldCheck, Table2 } from "lucide-react";
import { useState } from "react";
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
  ErrorNote,
  ErrorState,
  LoadingRows,
  PageHeader,
  Select,
  StatusBadge,
  TextField,
  Toolbar,
  fmtDate,
  fmtDateTime,
} from "../ui";
import { BentoSplit } from "../layout";

const PREVIEW_ROWS = 50;

export function HandoffPage() {
  const { can } = useAuth();
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);
  const [targetType, setTargetType] = useState("");
  const [status, setStatus] = useState("");
  const [since, setSince] = useState(monthAgo);
  const [until, setUntil] = useState(today);
  const [requested, setRequested] = useState<Record<string, string> | null>(null);
  const fieldMap = useQuery({ queryKey: ["crm-field-map"], queryFn: () => api("/integration/crm/field-map") });
  const params = { target_type: targetType, status, since, until };
  const preview = useQuery({
    queryKey: ["crm-preview", requested],
    queryFn: () => api(`/integration/crm/records${query({ ...requested!, limit: 1000 })}`),
    enabled: requested !== null,
  });
  const audiences =
    can(P.NBA_READ_ALL) || (can(P.NBA_READ_HCP_ASSIGNED) && can(P.NBA_READ_PATIENT_ASSIGNED))
      ? ["", "HCP", "PATIENT"]
      : null;
  const downloadUrl = (format: "csv" | "json") =>
    `/api/integration/crm/records${query({ ...params, limit: 5000, format })}`;
  const d: Json | undefined = preview.data;

  return (
    <>
      <PageHeader
        title="CRM hand-off"
        subtitle="Approved recommendations and their delivery outcomes, in the fixed format a CRM or marketing-automation system would read. Nothing is sent anywhere from here: you preview or download, and every request is recorded in the audit log."
      />
      <BentoSplit
        ratio="8/4"
        main={
          <>
            <Card title="Export" description="Recommendations approved in the date range, within your own scope.">
              <Toolbar>
                {audiences && (
                  <Select label="Audience" value={targetType} onChange={(e) => setTargetType(e.target.value)}>
                    <option value="">HCPs and patients</option>
                    <option value="HCP">HCPs</option>
                    <option value="PATIENT">Patients</option>
                  </Select>
                )}
                <Select label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
                  <option value="">Approved, sent or responded</option>
                  <option value="approved">Approved, not yet sent</option>
                  <option value="sent">Sent, no response yet</option>
                  <option value="responded">Responded</option>
                </Select>
                <TextField label="From" type="date" value={since} max={until} onChange={(e) => setSince(e.target.value)} />
                <TextField label="To" type="date" value={until} min={since} max={today} onChange={(e) => setUntil(e.target.value)} />
              </Toolbar>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="inline-flex items-center gap-2 rounded-[10px] bg-primary px-3.5 py-2 text-sm font-semibold text-on-primary hover:bg-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2"
                  onClick={() => setRequested({ ...params })}
                >
                  <Table2 className="h-4 w-4" aria-hidden /> Preview
                </button>
                <a
                  href={downloadUrl("csv")}
                  download
                  className="inline-flex items-center gap-2 rounded-[10px] px-3.5 py-2 text-sm font-semibold text-ink ring-1 ring-line hover:bg-subtle"
                >
                  <Download className="h-4 w-4" aria-hidden /> Download CSV
                </a>
                <a
                  href={downloadUrl("json")}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 rounded-[10px] px-3.5 py-2 text-sm font-semibold text-ink ring-1 ring-line hover:bg-subtle"
                >
                  <FileJson className="h-4 w-4" aria-hidden /> Open JSON
                </a>
              </div>
            </Card>
            {requested && (
              <Card
                className="mt-6"
                title="Preview"
                description={
                  d ? `${d.count} record${d.count === 1 ? "" : "s"}${d.count > PREVIEW_ROWS ? `, first ${PREVIEW_ROWS} shown` : ""}${d.truncated ? " (limit reached: narrow the range)" : ""}.` : undefined
                }
              >
                {preview.isLoading ? (
                  <LoadingRows rows={5} />
                ) : preview.error ? (
                  <ErrorNote error={preview.error} />
                ) : d.count === 0 ? (
                  <EmptyState title="No approved recommendations in this range" icon={<Table2 className="h-5 w-5" />}>
                    Only recommendations a person approved are handed off.
                  </EmptyState>
                ) : (
                  <DataTable
                    columns={[
                      { key: "id", header: "Record", primary: true, cell: (r: Json) => <span className="tabular">{r.record_id}</span> },
                      { key: "t", header: "Target", cell: (r: Json) => <span className="tabular">{r.target_id}</span> },
                      { key: "a", header: "Action", cell: (r: Json) => `${r.action_label} · ${r.channel}` },
                      { key: "c", header: "Content", cell: (r: Json) => <span className="tabular">{r.content_id} v{r.content_version}</span> },
                      { key: "s", header: "Status", cell: (r: Json) => <StatusBadge status={r.status} /> },
                      { key: "d", header: "Approved", cell: (r: Json) => fmtDateTime(r.approved_at) },
                      { key: "o", header: "Outcome", cell: (r: Json) => r.outcome ?? "—" },
                      { key: "x", header: "Data", cell: (r: Json) => (r.synthetic ? <Badge>Synthetic</Badge> : <Badge tone="info">Real</Badge>) },
                    ]}
                    rows={d.records.slice(0, PREVIEW_ROWS)}
                    rowKey={(r) => r.record_id}
                    caption="Hand-off records preview"
                  />
                )}
              </Card>
            )}
          </>
        }
        aside={
          <>
            <Alert tone="info" icon={<ShieldCheck className="h-4 w-4" />} title="What never leaves the engine">
              Names, dates of birth, contact details, conditions, medicine names, adherence figures, rationale text and the
              delivered wording. A patient's response is reduced to engaged, no response or declined; patients appear only
              under their pseudonymous id.
            </Alert>
            <Card title="Field map" description="The contract a CRM connector would implement.">
              {fieldMap.isLoading ? (
                <LoadingRows rows={6} />
              ) : fieldMap.error ? (
                <ErrorState error={fieldMap.error} retry={() => void fieldMap.refetch()} title="The field map could not be loaded" />
              ) : (
                <ul className="divide-y divide-line">
                  {fieldMap.data.fields.map((f: Json) => (
                    <li key={f.name} className="py-2.5 first:pt-0 last:pb-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <code className="text-[13px] font-semibold text-ink">{f.name}</code>
                        <span className="text-xs text-ink-subtle">{f.type}</span>
                        {f.hcp_only && <Badge>HCP only</Badge>}
                      </div>
                      <div className="mt-0.5 text-[13px] text-ink-muted">{f.description}</div>
                      <div className="text-xs text-ink-subtle">Typical CRM use: {f.crm_use}</div>
                    </li>
                  ))}
                </ul>
              )}
              {fieldMap.data && (
                <p className="mt-4 text-xs text-ink-subtle">
                  Up to {fieldMap.data.max_rows} records and {fieldMap.data.max_range_days} days per request. Times are UTC.
                  Today: {fmtDate(today)}.
                </p>
              )}
            </Card>
          </>
        }
      />
    </>
  );
}
