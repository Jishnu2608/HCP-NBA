import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock3, Inbox, XCircle } from "lucide-react";
import { useId, useState } from "react";
import { api, patch, query } from "../api";
import type { Json } from "../api";
import { ROLE_LABEL } from "../auth";
import type { Role } from "../auth";
import { useToast } from "../toast";
import {
  Alert,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  ErrorState,
  FormField,
  KpiGrid,
  LoadingRows,
  PageHeader,
  Segmented,
  Stat,
  fmtDate,
  fmtDateTime,
} from "../ui";
import { RequestStatus } from "./Privacy";

function RequestCard({ request }: { request: Json }) {
  const client = useQueryClient();
  const toast = useToast();
  const id = useId();
  const [resolution, setResolution] = useState("");
  const update = useMutation({
    mutationFn: (status: string) => patch(`/admin/privacy-requests/${request.id}`, { status, resolution: resolution.trim() || null }),
    onSuccess: (_, status) => {
      toast(status === "in_review" ? "Marked in review" : "Request closed");
      setResolution("");
      void client.invalidateQueries({ queryKey: ["privacy-requests"] });
    },
  });
  const open = request.status === "submitted" || request.status === "in_review";
  return (
    <li className="rounded-xl border border-line bg-surface p-4 shadow-card sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-semibold text-ink">{request.type_label}</div>
          <div className="mt-0.5 break-words text-[13px] text-ink-subtle">
            {request.requester.name} · {request.requester.email} · {ROLE_LABEL[request.requester.role as Role]}
            {request.jurisdiction && ` · ${request.jurisdiction}`}
          </div>
        </div>
        <RequestStatus status={request.status} />
      </div>
      {request.details && <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-ink-muted">{request.details}</p>}
      <div className="mt-3 text-[13px] text-ink-subtle">
        Submitted {fmtDateTime(`${request.created_at}Z`)}
        {request.respond_by && ` · respond by ${fmtDate(request.respond_by)}`}
      </div>
      {request.resolution && (
        <p className="mt-3 rounded-lg bg-subtle p-3 text-sm text-ink">
          <span className="font-semibold">Outcome: </span>
          {request.resolution}
        </p>
      )}
      {open && (
        <div className="mt-4 space-y-3 border-t border-line pt-4">
          <FormField label="What was done" htmlFor={id} hint="Required to close the request. Shown to the requester.">
            <textarea
              id={id}
              rows={2}
              maxLength={2000}
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
              className="block w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink focus:border-primary focus:outline-none focus:ring-[3px] focus:ring-primary/20"
            />
          </FormField>
          <ErrorNote error={update.error} />
          <div className="flex flex-wrap gap-2">
            {request.status === "submitted" && (
              <Button size="sm" busy={update.isPending && update.variables === "in_review"} onClick={() => update.mutate("in_review")}>
                <Clock3 className="h-3.5 w-3.5" aria-hidden /> Mark in review
              </Button>
            )}
            <Button size="sm" variant="primary" busy={update.isPending && update.variables === "completed"} onClick={() => update.mutate("completed")}>
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Complete
            </Button>
            <Button size="sm" variant="quiet-danger" busy={update.isPending && update.variables === "rejected"} onClick={() => update.mutate("rejected")}>
              <XCircle className="h-3.5 w-3.5" aria-hidden /> Reject
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

export default function PrivacyRequests() {
  const [status, setStatus] = useState("open");
  const list = useQuery({
    queryKey: ["privacy-requests", status],
    queryFn: () => api(`/admin/privacy-requests${query({ status: status === "open" || status === "all" ? undefined : status })}`),
    placeholderData: (previous) => previous,
  });
  const counts: Record<string, number> = list.data?.counts ?? {};
  const items: Json[] = (list.data?.items ?? []).filter(
    (i: Json) => status !== "open" || i.status === "submitted" || i.status === "in_review",
  );
  const shown = (n?: number) => (list.data ? String(n ?? 0) : "—");

  return (
    <>
      <PageHeader
        title="Privacy requests"
        subtitle="Requests to access, correct, delete, restrict, export or object to the processing of personal information. Work each one and record what was done; nothing is carried out automatically."
      />
      <KpiGrid>
        <Stat label="Submitted" value={shown(counts.submitted)} tone={counts.submitted ? "warn" : undefined} icon={<Inbox className="h-4 w-4" aria-hidden />} hint="waiting for review" />
        <Stat label="In review" value={shown(counts.in_review)} icon={<Clock3 className="h-4 w-4" aria-hidden />} hint="being worked" />
        <Stat label="Completed" value={shown(counts.completed)} tone="ok" icon={<CheckCircle2 className="h-4 w-4" aria-hidden />} hint="closed with an outcome" />
        <Stat label="Rejected" value={shown(counts.rejected)} icon={<XCircle className="h-4 w-4" aria-hidden />} hint="closed with a reason" />
      </KpiGrid>
      <Alert tone="info" className="mb-6">
        Response deadlines and how each request type is carried out (for example account deletion and what records must
        be kept) are to be confirmed with legal counsel.
      </Alert>
      <Card flush title="Requests">
        <div className="min-w-0 border-b border-line px-4 py-3 sm:px-5">
          <Segmented
            label="Filter by status"
            value={status}
            onChange={setStatus}
            options={[
              { value: "open", label: "Open" },
              { value: "all", label: "All" },
              { value: "completed", label: "Completed" },
              { value: "rejected", label: "Rejected" },
            ]}
          />
        </div>
        <div className="p-4 sm:p-5">
          {list.isLoading ? (
            <LoadingRows rows={3} label="Loading requests" />
          ) : list.error ? (
            <ErrorState error={list.error} retry={() => void list.refetch()} title="Requests could not be loaded" />
          ) : items.length ? (
            <ul className="space-y-4">
              {items.map((r) => (
                <RequestCard key={r.id} request={r} />
              ))}
            </ul>
          ) : (
            <EmptyState title="Nothing here" icon={<Inbox className="h-5 w-5" />}>
              No requests with this status.
            </EmptyState>
          )}
        </div>
      </Card>
    </>
  );
}
