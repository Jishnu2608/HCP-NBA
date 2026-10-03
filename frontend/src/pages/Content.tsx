import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarX2, CheckCircle2, ClipboardCheck, Clock3, FileCheck2, Hourglass, Lock, XCircle } from "lucide-react";
import { useState } from "react";
import { api, post } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { P } from "../permissions";
import { useToast } from "../toast";
import {
  Alert,
  Badge,
  Button,
  Card,
  ChannelIcon,
  EmptyState,
  ErrorNote,
  ErrorState,
  Loading,
  MlrBadge,
  PageHeader,
  Segmented,
  Stat,
  TextArea,
  Toolbar,
  channelName,
  cx,
  fmtDate,
  num,
  titleCase,
} from "../ui";

type View = "attention" | "all" | "HCP" | "PATIENT";
const VIEWS: Array<{ key: View; label: string }> = [
  { key: "attention", label: "Needs attention" },
  { key: "all", label: "All content" },
  { key: "HCP", label: "HCP audience" },
  { key: "PATIENT", label: "Patient audience" },
];

function rail(c: Json) {
  if (c.is_expired || c.mlr_status === "rejected") return "bg-bad";
  if (c.mlr_status === "pending") return "bg-warn";
  return "bg-ok";
}

export default function ContentLibrary() {
  const { can } = useAuth();
  const client = useQueryClient();
  const toast = useToast();
  const canReview = can(P.CONTENT_APPROVE);
  const governance = can(P.CONTENT_READ_ALL);
  const [view, setView] = useState<View>(governance ? "attention" : "all");
  const [open, setOpen] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const list = useQuery({ queryKey: ["content"], queryFn: () => api<Json[]>("/content") });
  const review = useMutation({
    mutationFn: (v: { id: string; decision: string }) =>
      post(`/content/${v.id}/review`, { decision: v.decision, comment: comment || null }),
    onSuccess: (_, v) => {
      toast(v.decision === "approve" ? `${v.id} approved for use` : `${v.id} rejected`);
      setComment("");
      setOpen(null);
      void client.invalidateQueries({ queryKey: ["content"] });
    },
  });

  if (list.isLoading) return <Loading label="Loading content" />;
  if (list.error) return <ErrorState error={list.error} retry={() => void list.refetch()} variant="page" title="Content could not be loaded" />;
  const all = list.data ?? [];
  const rows = all.filter((c) => {
    if (view === "attention") return !c.usable;
    if (view === "all") return true;
    return c.audience === view;
  });
  const pending = all.filter((c) => c.mlr_status === "pending").length;
  const expired = all.filter((c) => c.is_expired).length;
  const waiting = all.reduce((n, c) => n + (c.recommendations_waiting ?? 0), 0);
  const usable = all.filter((c) => c.usable).length;

  return (
    <>
      <PageHeader
        title={governance ? "Content and MLR review" : "Approved content"}
        subtitle={
          governance
            ? "Only content that is MLR-approved and inside its validity window can appear in a recommendation. Approving an item unlocks the recommendations waiting on it at the next engine cycle."
            : "Content cleared for use with your audience. Anything not listed here cannot be sent."
        }
      />

      {governance && (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Pending MLR review" value={num(pending)} tone={pending ? "warn" : undefined} icon={<Hourglass className="h-4 w-4" aria-hidden />} />
            <Stat label="Approval expired" value={num(expired)} tone={expired ? "bad" : undefined} icon={<CalendarX2 className="h-4 w-4" aria-hidden />} />
            <Stat
              label="Recommendations waiting"
              value={num(waiting)}
              hint="on content not yet usable"
              icon={<Clock3 className="h-4 w-4" aria-hidden />}
            />
            <Stat label="Approved and in date" value={`${usable} of ${all.length}`} tone="ok" icon={<CheckCircle2 className="h-4 w-4" aria-hidden />} />
          </div>
          {!canReview && (
            <Alert tone="info" icon={<Lock className="h-5 w-5" aria-hidden />} className="mb-6">
              <span className="text-info">
                You can see every item and its status. Approving or rejecting content is reserved for Compliance / MLR
                reviewers.
              </span>
            </Alert>
          )}
          <Toolbar>
            <Segmented
              label="Content view"
              value={view}
              onChange={setView}
              options={VIEWS.map((v) => ({
                value: v.key,
                label: v.label,
                count:
                  v.key === "attention"
                    ? all.filter((c) => !c.usable).length
                    : v.key === "all"
                      ? all.length
                      : all.filter((c) => c.audience === v.key).length,
              }))}
            />
          </Toolbar>
        </>
      )}

      <ErrorNote error={review.error} className="mb-4" />

      <Card flush>
        {!rows.length ? (
          <EmptyState
            tone="ok"
            icon={<ClipboardCheck className="h-5 w-5" />}
            title={view === "attention" ? "Nothing needs attention" : "No content in this view"}
          >
            {view === "attention" ? "Every item is approved and inside its validity window." : undefined}
          </EmptyState>
        ) : (
          <ul className="divide-y divide-line">
            {rows.map((c) => {
              const reviewable = canReview && (c.mlr_status === "pending" || c.is_expired);
              const isOpen = open === c.content_id;
              return (
                <li key={c.content_id} className="relative px-5 py-5 sm:px-6">
                  <span className={cx("absolute inset-y-4 left-0 w-1 rounded-r-full", rail(c))} aria-hidden />
                  <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <h2 className="text-[15px] font-semibold text-ink">{c.title}</h2>
                        <span className="tabular text-xs text-ink-subtle">{c.content_id}</span>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        <MlrBadge status={c.mlr_status} expired={c.is_expired} />
                        <Badge tone="sage">{c.audience === "HCP" ? "HCP audience" : "Patient audience"}</Badge>
                        <Badge>{titleCase(c.action_type)}</Badge>
                        {c.recommendations_waiting > 0 && (
                          <Badge tone="warn" icon={<Clock3 className="h-3.5 w-3.5" aria-hidden />}>
                            {c.recommendations_waiting} recommendations waiting
                          </Badge>
                        )}
                      </div>
                      <p className="mt-3 max-w-3xl text-sm leading-6 text-ink-muted">{c.body}</p>
                      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[13px] text-ink-subtle">
                        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                          {c.channels.map((ch: string) => (
                            <span key={ch} className="inline-flex items-center gap-1">
                              <ChannelIcon channel={ch} className="h-3.5 w-3.5" /> {channelName(ch)}
                            </span>
                          ))}
                        </span>
                        <span className="tabular">
                          {c.effective_date
                            ? `Effective ${fmtDate(c.effective_date)} · ${c.is_expired ? "expired" : "valid until"} ${fmtDate(c.expiry_date)}`
                            : "No approval on record"}
                        </span>
                      </div>
                    </div>
                    {reviewable && !isOpen && (
                      <Button variant="primary" className="shrink-0 self-start" onClick={() => setOpen(c.content_id)}>
                        <FileCheck2 className="h-4 w-4" aria-hidden /> Review
                      </Button>
                    )}
                  </div>
                  {isOpen && (
                    <div className="animate-rise mt-5 rounded-xl border border-line bg-subtle/60 p-4 sm:p-5">
                      <TextArea
                        label="Review comment"
                        hint="Optional. Recorded in the audit trail with your decision."
                        value={comment}
                        onChange={(e) => setComment(e.target.value)}
                        rows={3}
                        autoFocus
                      />
                      <div className="mt-4 flex flex-wrap gap-2">
                        <Button
                          variant="primary"
                          busy={review.isPending}
                          onClick={() => review.mutate({ id: c.content_id, decision: "approve" })}
                        >
                          <CheckCircle2 className="h-4 w-4" aria-hidden /> Approve for use
                        </Button>
                        <Button
                          variant="quiet-danger"
                          busy={review.isPending}
                          onClick={() => review.mutate({ id: c.content_id, decision: "reject" })}
                        >
                          <XCircle className="h-4 w-4" aria-hidden /> Reject
                        </Button>
                        <Button variant="ghost" onClick={() => setOpen(null)}>
                          Cancel
                        </Button>
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </>
  );
}
