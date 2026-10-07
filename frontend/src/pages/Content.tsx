import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, ClipboardCheck, Clock3, FilePlus2, Hourglass, MessageSquareReply, Undo2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { P } from "../permissions";
import {
  Badge,
  Button,
  Card,
  ChannelIcon,
  EmptyState,
  ErrorState,
  KpiGrid,
  Loading,
  MlrBadge,
  PageHeader,
  Segmented,
  Stat,
  Toolbar,
  channelName,
  cx,
  fmtDate,
  num,
  titleCase,
} from "../ui";

type ReviewView = "review" | "waiting" | "approved" | "attention" | "retired" | "all";
const REVIEW_VIEWS: Array<{ key: ReviewView; label: string; test: (c: Json) => boolean }> = [
  { key: "review", label: "To review", test: (c) => c.mlr_status === "pending" },
  { key: "waiting", label: "Waiting on author", test: (c) => c.mlr_status === "changes_requested" && c.is_latest },
  { key: "approved", label: "Approved, in date", test: (c) => c.usable },
  {
    key: "attention",
    label: "Expired or rejected",
    test: (c) => c.mlr_status === "rejected" || (c.mlr_status === "approved" && c.is_expired),
  },
  { key: "retired", label: "Withdrawn & superseded", test: (c) => ["withdrawn", "superseded"].includes(c.mlr_status) },
  { key: "all", label: "All", test: () => true },
];

type RepView = "approved" | "mine" | "delivered";

function rail(c: Json) {
  if (c.is_expired || ["rejected", "withdrawn"].includes(c.mlr_status)) return "bg-bad-fill";
  if (["pending", "changes_requested", "draft"].includes(c.mlr_status)) return "bg-warn-fill";
  if (c.mlr_status === "superseded") return "bg-line-strong";
  return "bg-ok-fill";
}

const DECISION_WORD: Record<string, string> = {
  approve: "Approved",
  request_changes: "Changes requested",
  reject: "Rejected",
  withdraw: "Withdrawn",
  request_revision: "Revision requested",
};

function ContentRow({ c, reviewer }: { c: Json; reviewer: boolean }) {
  const last = c.last_decision;
  return (
    <li className="relative">
      <span className={cx("absolute inset-y-4 left-0 w-1 rounded-r-full", rail(c))} aria-hidden />
      <Link
        to={`/content/${c.content_id}`}
        className="block px-5 py-5 transition-colors hover:bg-subtle/60 focus-visible:bg-subtle/60 sm:px-6"
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <h2 className="text-[15px] font-semibold text-ink">{c.title}</h2>
          <span className="tabular text-xs text-ink-subtle">
            {c.content_id} · v{c.version}
            {c.previous_id ? ` (revises ${c.previous_id})` : ""}
          </span>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <MlrBadge status={c.mlr_status} expired={c.mlr_status === "approved" && c.is_expired} />
          <Badge tone="sage">{c.audience === "HCP" ? "HCP audience" : "Patient audience"}</Badge>
          <Badge>{titleCase(c.action_type)}</Badge>
          {c.author && <Badge tone="info">Proposed by {c.mine ? "you" : c.author}</Badge>}
          {reviewer && c.recommendations_waiting > 0 && (
            <Badge tone="warn" icon={<Clock3 className="h-3.5 w-3.5" aria-hidden />}>
              {c.recommendations_waiting} recommendation{c.recommendations_waiting === 1 ? "" : "s"} waiting
            </Badge>
          )}
          {last?.new && <Badge tone="accent">New decision</Badge>}
          {c.unread > 0 && (
            <Badge tone="accent">
              {c.unread} new message{c.unread === 1 ? "" : "s"}
            </Badge>
          )}
          {c.delivered_by_you && !c.mine && <Badge>Delivered to your HCPs</Badge>}
          {c.product && <Badge tone="info">{c.product}</Badge>}
        </div>
        <p className="mt-3 line-clamp-2 max-w-3xl text-sm leading-6 text-ink-muted">{c.body}</p>
        {last && last.decision !== "approve" && last.feedback && (
          <p className="mt-2 max-w-3xl text-[13px] text-ink">
            <span className="font-semibold">{DECISION_WORD[last.decision] ?? last.decision}:</span> {last.feedback}
          </p>
        )}
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
              : c.submitted_at
                ? `Submitted ${fmtDate(c.submitted_at)}`
                : "Not approved"}
          </span>
        </div>
      </Link>
    </li>
  );
}

export default function ContentLibrary() {
  const { can } = useAuth();
  const reviewer = can(P.CONTENT_APPROVE);
  const governance = can(P.CONTENT_READ_ALL);
  const proposer = can(P.CONTENT_PROPOSE);
  const [view, setView] = useState<ReviewView>(reviewer ? "review" : "all");
  const [repView, setRepView] = useState<RepView>("approved");
  const list = useQuery({ queryKey: ["content"], queryFn: () => api<Json[]>("/content"), refetchInterval: 60_000 });

  if (list.isLoading) return <Loading label="Loading content" />;
  if (list.error) return <ErrorState error={list.error} retry={() => void list.refetch()} variant="page" title="Content could not be loaded" />;
  const all = list.data ?? [];
  const count = (k: ReviewView) => all.filter(REVIEW_VIEWS.find((v) => v.key === k)!.test).length;

  let rows: Json[];
  if (governance) rows = all.filter(REVIEW_VIEWS.find((v) => v.key === view)!.test);
  else if (proposer && repView === "mine") rows = all.filter((c) => c.mine);
  else if (proposer && repView === "delivered") rows = all.filter((c) => c.delivered_by_you);
  else rows = all.filter((c) => c.usable);
  const waiting = all.reduce((n, c) => n + (c.recommendations_waiting ?? 0), 0);
  const newDecisions = all.filter((c) => c.mine && (c.last_decision?.new || c.unread > 0)).length;
  const deliveredUnread = all.filter((c) => c.delivered_by_you && !c.mine && c.unread > 0).length;

  return (
    <>
      <PageHeader
        title={reviewer ? "Content and MLR review" : proposer ? "Content" : governance ? "Content" : "Approved content"}
        subtitle={
          reviewer
            ? "Review what representatives submit and maintain the library: approve, request changes, reject, withdraw. Only the approved version of each item, inside its validity window, can be delivered, and only as written."
            : proposer
              ? "Material approved for your HCPs, and the content you propose for MLR review. What an HCP receives is exactly the approved version."
              : "Content cleared for use with your audience. Anything not listed here cannot be sent."
        }
        action={
          proposer ? (
            <Link to="/content/new">
              <Button variant="primary">
                <FilePlus2 className="h-4 w-4" aria-hidden /> New proposal
              </Button>
            </Link>
          ) : undefined
        }
      />

      {governance && (
        <>
          <KpiGrid>
            <Stat label="To review" value={num(count("review"))} tone={count("review") ? "warn" : undefined} icon={<Hourglass className="h-4 w-4" aria-hidden />} hint="submitted, waiting for a decision" />
            <Stat label="Waiting on author" value={num(count("waiting"))} icon={<Undo2 className="h-4 w-4" aria-hidden />} hint="changes requested" />
            <Stat label="Approved and in date" value={`${count("approved")} of ${all.length}`} tone="ok" icon={<CheckCircle2 className="h-4 w-4" aria-hidden />} />
            <Stat
              label="Recommendations waiting"
              value={num(waiting)}
              hint="held back by content not yet usable"
              icon={<Clock3 className="h-4 w-4" aria-hidden />}
            />
          </KpiGrid>
          <Toolbar>
            <Segmented
              label="Content view"
              value={view}
              onChange={setView}
              options={REVIEW_VIEWS.map((v) => ({ value: v.key, label: v.label, count: count(v.key) }))}
            />
          </Toolbar>
        </>
      )}
      {!governance && proposer && (
        <Toolbar>
          <Segmented
            label="Content view"
            value={repView}
            onChange={setRepView}
            options={[
              { value: "approved", label: "Approved for use", count: all.filter((c) => c.usable).length },
              { value: "mine", label: newDecisions ? `My proposals · ${newDecisions} new` : "My proposals", count: all.filter((c) => c.mine).length },
              {
                value: "delivered",
                label: deliveredUnread ? `Delivered to my HCPs · ${deliveredUnread} new` : "Delivered to my HCPs",
                count: all.filter((c) => c.delivered_by_you).length,
              },
            ]}
          />
        </Toolbar>
      )}

      <Card flush>
        {!rows.length ? (
          <EmptyState
            tone="ok"
            icon={view === "review" ? <ClipboardCheck className="h-5 w-5" /> : <MessageSquareReply className="h-5 w-5" />}
            title={
              governance && view === "review"
                ? "Nothing waiting for review"
                : proposer && repView === "mine"
                  ? "You have not proposed any content"
                  : "No content in this view"
            }
          >
            {governance && view === "review"
              ? "New submissions and resubmitted versions appear here and in the menu count."
              : proposer && repView === "mine"
                ? "Use New proposal to submit material, with its claims and references, for MLR review."
                : undefined}
          </EmptyState>
        ) : (
          <ul className="divide-y divide-line">
            {rows.map((c) => (
              <ContentRow key={c.content_id} c={c} reviewer={reviewer} />
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
