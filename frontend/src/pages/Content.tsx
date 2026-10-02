import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, post } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import {
  Badge,
  Button,
  Card,
  ErrorNote,
  Loading,
  MlrBadge,
  PageHeader,
  cx,
  fmtDate,
  titleCase,
} from "../ui";

const VIEWS = [
  { key: "attention", label: "Needs attention" },
  { key: "all", label: "All content" },
  { key: "HCP", label: "HCP audience" },
  { key: "PATIENT", label: "Patient audience" },
];

export default function ContentLibrary() {
  const { user } = useAuth();
  const client = useQueryClient();
  const canReview = user!.role === "compliance";
  const governance = canReview || user!.role === "admin";
  const [view, setView] = useState(governance ? "attention" : "all");
  const [open, setOpen] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const list = useQuery({ queryKey: ["content"], queryFn: () => api<Json[]>("/content") });
  const review = useMutation({
    mutationFn: (v: { id: string; decision: string }) =>
      post(`/content/${v.id}/review`, { decision: v.decision, comment: comment || null }),
    onSuccess: () => {
      setComment("");
      setOpen(null);
      void client.invalidateQueries({ queryKey: ["content"] });
    },
  });

  if (list.isLoading) return <Loading />;
  if (list.error) return <ErrorNote error={list.error} />;
  const rows = (list.data ?? []).filter((c) => {
    if (view === "attention") return !c.usable;
    if (view === "all") return true;
    return c.audience === view;
  });

  return (
    <>
      <PageHeader
        title={governance ? "Content and MLR review" : "Approved content"}
        subtitle={
          governance
            ? "Only content that is MLR-approved and inside its validity window can appear in a recommendation. Approving here unlocks the recommendations waiting on it at the next engine cycle."
            : "Content cleared for use with your audience. Anything not listed here cannot be sent."
        }
      />
      {governance && (
        <div className="mb-3 flex flex-wrap gap-2">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              onClick={() => setView(v.key)}
              className={cx(
                "rounded-full px-3 py-1.5 text-sm font-medium",
                view === v.key
                  ? "bg-brand-600 text-white"
                  : "bg-white text-stone-600 ring-1 ring-inset ring-stone-200 hover:bg-stone-50",
              )}
            >
              {v.label}
            </button>
          ))}
        </div>
      )}
      <ErrorNote error={review.error} />
      <div className="mt-3 grid gap-3">
        {rows.map((c) => (
          <Card key={c.content_id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-stone-400">{c.content_id}</span>
                  <h3 className="text-sm font-semibold text-stone-900">{c.title}</h3>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <MlrBadge status={c.mlr_status} expired={c.is_expired} />
                  <Badge>{c.audience === "HCP" ? "HCP audience" : "Patient audience"}</Badge>
                  <Badge>{titleCase(c.action_type)}</Badge>
                  {c.channels.map((ch: string) => (
                    <Badge key={ch}>{titleCase(ch)}</Badge>
                  ))}
                  {c.recommendations_waiting > 0 && (
                    <Badge tone="warn">{c.recommendations_waiting} recommendations waiting</Badge>
                  )}
                </div>
                <p className="mt-2 max-w-3xl text-sm text-stone-600">{c.body}</p>
                <p className="mt-1.5 text-xs text-stone-500">
                  {c.effective_date
                    ? `Effective ${fmtDate(c.effective_date)} · ${c.is_expired ? "expired" : "valid until"} ${fmtDate(c.expiry_date)}`
                    : "No approval on record"}
                </p>
              </div>
              {canReview && (c.mlr_status === "pending" || c.is_expired) && open !== c.content_id && (
                <Button onClick={() => setOpen(c.content_id)}>Review</Button>
              )}
            </div>
            {open === c.content_id && (
              <div className="mt-4 border-t border-stone-100 pt-4">
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  rows={2}
                  placeholder="Review comment (recorded in the audit trail)"
                  className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
                />
                <div className="mt-2 flex gap-2">
                  <Button
                    variant="primary"
                    busy={review.isPending}
                    onClick={() => review.mutate({ id: c.content_id, decision: "approve" })}
                  >
                    Approve for use
                  </Button>
                  <Button
                    variant="danger"
                    busy={review.isPending}
                    onClick={() => review.mutate({ id: c.content_id, decision: "reject" })}
                  >
                    Reject
                  </Button>
                  <Button variant="ghost" onClick={() => setOpen(null)}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </Card>
        ))}
        {!rows.length && (
          <Card>
            <p className="text-sm text-stone-500">Nothing here. Every item is approved and in date.</p>
          </Card>
        )}
      </div>
    </>
  );
}
