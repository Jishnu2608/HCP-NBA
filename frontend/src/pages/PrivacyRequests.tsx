import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock3, Inbox, XCircle } from "lucide-react";
import { useId, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, patch, post, query } from "../api";
import type { Json } from "../api";
import { ROLE_LABEL } from "../auth";
import type { Role } from "../auth";
import { useToast } from "../toast";
import {
  Alert,
  Badge,
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
import { DeleteAccount } from "./DeleteAccount";
import { specialtyText } from "./HealthForms";
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
            {request.requester
              ? `${request.requester.name} · ${request.requester.email} · ${ROLE_LABEL[request.requester.role as Role]}`
              : (request.subject_label ?? "Deleted account")}
            {request.jurisdiction && ` · ${request.jurisdiction}`}
          </div>
        </div>
        <RequestStatus status={request.status} />
      </div>
      {request.details && <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-ink-muted">{request.details}</p>}
      <div className="mt-3 text-[13px] text-ink-subtle">
        Submitted {fmtDateTime(request.created_at)}
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
          {request.type === "erasure" && request.requester?.deletable && (
            <DeleteAccount account={request.requester} privacyRequestId={request.id} />
          )}
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

function SpecialtyRequestCard({ request }: { request: Json }) {
  const client = useQueryClient();
  const toast = useToast();
  const id = useId();
  const [notes, setNotes] = useState("");
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") =>
      post(`/admin/specialty-requests/${request.id}/${decision}`, { notes: notes.trim() || null }),
    onSuccess: (_, decision) => {
      toast(decision === "approve" ? "Approved: the HCP's specialties are updated" : "Request rejected");
      setNotes("");
      void client.invalidateQueries({ queryKey: ["specialty-requests"] });
      void client.invalidateQueries({ queryKey: ["users"] });
    },
  });
  const open = request.status === "pending";
  return (
    <li className="rounded-xl border border-line bg-surface p-4 shadow-card sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-semibold text-ink">
            {ACTION_TEXT[request.action]} · {request.hcp_name}
          </div>
          <div className="mt-0.5 text-[13px] text-ink-subtle">
            Requested by {request.requested_by ?? "a deleted account"} · {fmtDateTime(request.created_at)}
          </div>
        </div>
        <Badge tone={request.status === "approved" ? "ok" : request.status === "rejected" ? "bad" : "warn"}>
          {request.status === "pending" ? "Pending" : request.status === "approved" ? "Approved" : "Rejected"}
        </Badge>
      </div>
      <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-[13px] text-ink-subtle">When requested</dt>
          <dd className="text-ink">{specialtyText(request.previous)}</dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Requested</dt>
          <dd className="font-semibold text-ink">{specialtyText(request.requested)}</dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Now</dt>
          <dd className="text-ink">{specialtyText(request.current)}</dd>
        </div>
      </dl>
      {request.notes && <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-ink-muted">“{request.notes}”</p>}
      {open && request.open_consultations_affected > 0 && (
        <Alert tone="warn" title="Open consultations are affected" className="mt-3">
          {request.open_consultations_affected} consultation{request.open_consultations_affected === 1 ? " waiting" : "s waiting"} for
          this HCP would no longer suit the new specialties. Approving returns{" "}
          {request.open_consultations_affected === 1 ? "it" : "them"} to the care manager to route again; the patient is told
          another professional will be arranged.
        </Alert>
      )}
      {!open && (
        <p className="mt-3 rounded-lg bg-subtle p-3 text-sm text-ink">
          <span className="font-semibold">Decided by {request.reviewer ?? "an administrator"}</span>
          {request.decided_at && ` on ${fmtDate(request.decided_at)}`}
          {request.decision_notes && `: ${request.decision_notes}`}
        </p>
      )}
      {open && (
        <div className="mt-4 space-y-3 border-t border-line pt-4">
          <FormField label="Decision notes (optional)" htmlFor={id} hint="Shown to the HCP.">
            <textarea
              id={id}
              rows={2}
              maxLength={500}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="block w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink focus:border-primary focus:outline-none focus:ring-[3px] focus:ring-primary/20"
            />
          </FormField>
          <ErrorNote error={decide.error} />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="primary" busy={decide.isPending && decide.variables === "approve"} onClick={() => decide.mutate("approve")}>
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Approve
            </Button>
            <Button size="sm" variant="quiet-danger" busy={decide.isPending && decide.variables === "reject"} onClick={() => decide.mutate("reject")}>
              <XCircle className="h-3.5 w-3.5" aria-hidden /> Reject
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

const ACTION_TEXT: Record<string, string> = {
  add: "Add specialties",
  remove: "Remove specialties",
  replace: "Replace specialties",
};

function SpecialtyRequests() {
  const [status, setStatus] = useState("pending");
  const list = useQuery({
    queryKey: ["specialty-requests", "admin", status],
    queryFn: () => api(`/admin/specialty-requests${query({ status: status === "all" ? undefined : status })}`),
    placeholderData: (previous) => previous,
    refetchInterval: 30_000,
  });
  const counts: Record<string, number> = list.data?.counts ?? {};
  const items: Json[] = list.data?.items ?? [];
  return (
    <Card flush title="HCP specialty change requests">
      <div className="min-w-0 border-b border-line px-4 py-3 sm:px-5">
        <Segmented
          label="Filter by status"
          value={status}
          onChange={setStatus}
          options={[
            { value: "pending", label: "Pending", count: counts.pending ?? 0 },
            { value: "approved", label: "Approved", count: counts.approved ?? 0 },
            { value: "rejected", label: "Rejected", count: counts.rejected ?? 0 },
            { value: "all", label: "All" },
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
              <SpecialtyRequestCard key={r.id} request={r} />
            ))}
          </ul>
        ) : (
          <EmptyState title="Nothing here" icon={<Inbox className="h-5 w-5" />}>
            HCPs ask here when their specialties should change. Approving changes them; nothing changes otherwise.
          </EmptyState>
        )}
      </div>
    </Card>
  );
}

/** The administrator's request centre: privacy requests and HCP specialty changes. */
export default function RequestCentre() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "specialties" ? "specialties" : "privacy";
  return (
    <>
      <div className="mb-4">
        <Segmented
          label="Request type"
          value={tab}
          onChange={(v) => setParams(v === "privacy" ? {} : { tab: v }, { replace: true })}
          options={[
            { value: "privacy", label: "Privacy" },
            { value: "specialties", label: "Specialty changes" },
          ]}
        />
      </div>
      {tab === "privacy" ? <PrivacyRequests /> : (
        <>
          <PageHeader
            title="Specialty change requests"
            subtitle="HCPs cannot change their own specialties; they ask here. Approve only what you can verify. An approval applies the requested list, and is refused if the specialties changed since the request was made."
          />
          <SpecialtyRequests />
        </>
      )}
    </>
  );
}

function PrivacyRequests() {
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
