import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, ClipboardCheck, Clock3, FilePlus2, Hourglass, MessageSquareReply, Undo2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { BarList, ChartPanel, ChartTable, Columns, useInsights } from "../charts";
import { BentoGrid, SectionHeader } from "../layout";
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

const days = (n: number) => `${n} day${n === 1 ? "" : "s"}`;
const dayText = (n: number) => (n < 1 ? "under 1 day" : days(n));

/** What needs the reviewer first: submissions by how long they have waited, and approvals
 *  about to end (GET /api/insights/content). No review-time target exists, so nothing is
 *  marked late. */
function ReviewAttention({ q }: { q: ReturnType<typeof useInsights> }) {
  const navigate = useNavigate();
  const d: Json | undefined = q.data;
  const waiting: Json[] = d?.waiting ?? [];
  const expiring: Json[] = d?.expiring ?? [];
  return (
    <BentoGrid className="mb-6">
      <ChartPanel
        span="half"
        title="Waiting for a decision"
        question="Submitted versions by how long they have waited, longest first. Select one to open it."
        query={q}
        empty={d && !waiting.length ? "Nothing is waiting for review." : false}
        table={
          <ChartTable
            caption="Submissions waiting for a decision"
            head={["Content", "Submitted", "Days waiting"]}
            rows={waiting.map((w) => [`${w.content_id} v${w.version}`, fmtDate(w.submitted_at), w.days_waiting ?? "—"])}
          />
        }
      >
        <BarList
          items={waiting.slice(0, 8).map((w) => ({
            key: w.content_id,
            label: `${w.content_id} v${w.version}`,
            sub: `${w.title}${w.claimed ? " · in review" : " · not started"}${w.submitted_at ? "" : " · submission date not recorded"}`,
            value: w.days_waiting ?? 0,
            figure: w.days_waiting != null ? days(Math.round(w.days_waiting)) : "—",
            tone: "warn",
            tip: { title: w.title, lines: [`${w.content_id} version ${w.version}`, `Submitted ${fmtDate(w.submitted_at)}`, w.claimed ? "A reviewer has started" : "No reviewer has started"] },
            onOpen: () => navigate(`/content/${w.content_id}`),
          }))}
        />
      </ChartPanel>
      <ChartPanel
        span="half"
        title="Approvals ending soon"
        question={`Approved material whose approval ends within ${d?.expiry_window_days ?? 60} days. Days remaining, not a percentage.`}
        query={q}
        empty={d && !expiring.length ? `No approval ends in the next ${d.expiry_window_days} days.` : false}
        table={
          <ChartTable
            caption="Approvals ending soon"
            head={["Content", "Audience", "Ends", "Days left"]}
            rows={expiring.map((e) => [`${e.content_id} v${e.version}`, e.audience, fmtDate(e.expiry_date), e.days_remaining])}
          />
        }
      >
        <BarList
          max={d?.expiry_window_days}
          items={expiring.slice(0, 8).map((e) => ({
            key: e.content_id,
            label: `${e.content_id} v${e.version}`,
            sub: e.title,
            value: e.days_remaining,
            figure: `${days(e.days_remaining)} left`,
            tone: e.days_remaining <= 14 ? "bad" : "warn",
            tip: {
              title: e.title,
              lines: [
                `${e.content_id} version ${e.version} · ${e.audience} audience`,
                `Approved ${fmtDate(e.approved_at)}, ends ${fmtDate(e.expiry_date)}`,
                e.jurisdictions?.length ? `Countries: ${e.jurisdictions.join(", ")}` : "Not restricted by country",
              ],
            },
            onOpen: () => navigate(`/content/${e.content_id}`),
          }))}
        />
      </ChartPanel>
    </BentoGrid>
  );
}

/** How reviews went: time from submission to decision, and which perspective raised
 *  concerns (last 90 days). */
function ReviewHistory({ q }: { q: ReturnType<typeof useInsights> }) {
  const d: Json | undefined = q.data;
  const t: Json | undefined = d?.turnaround;
  const c: Json | undefined = d?.concerns;
  return (
    <>
      <SectionHeader title="Review history" description={`Decisions in the last ${t?.window_days ?? 90} days.`} />
      <BentoGrid>
        <ChartPanel
          span="half"
          title="Time to decision"
          question="Days from submission to the MLR decision, per decided version."
          query={q}
          empty={t && !t.items.length ? "No version was decided in this period." : false}
          note={
            t &&
            (t.summary.median != null
              ? `Median ${dayText(t.summary.median)} (range ${dayText(t.summary.min)} to ${dayText(t.summary.max)}) over ${t.summary.n} decisions.`
              : `${t.summary.n} decision${t.summary.n === 1 ? "" : "s"} so far; a median appears from 3.`)
          }
          table={
            t && (
              <ChartTable
                caption="Time to decision per version"
                head={["Content", "Decision", "Submitted", "Decided", "Days"]}
                rows={t.items.map((i: Json) => [`${i.content_id} v${i.version}`, i.status, fmtDate(i.submitted_at), fmtDate(i.decided_at), i.days])}
              />
            )
          }
        >
          {t && (
            <Columns
              items={t.bins.map((b: Json) => ({
                key: b.key,
                label: b.label,
                value: b.value,
                tone: "info",
                tip: { title: b.label, lines: [`${b.value} decision${b.value === 1 ? "" : "s"}`] },
              }))}
            />
          )}
        </ChartPanel>
        <ChartPanel
          span="half"
          title="Concerns by perspective"
          question="How often each perspective recorded a concern in a decision."
          query={q}
          empty={c && !c.assessed_reviews ? "No decision recorded the medical, legal and regulatory perspectives in this period." : false}
          note={c && `Out of ${c.assessed_reviews} decision${c.assessed_reviews === 1 ? "" : "s"} with the perspectives recorded.`}
          table={
            c && (
              <ChartTable
                caption="Concerns by perspective"
                head={["Perspective", "Concerns", "Content"]}
                rows={c.perspectives.map((p: Json) => [p.label, p.value, p.content_ids.join(", ") || "—"])}
              />
            )
          }
        >
          {c && (
            <BarList
              max={c.assessed_reviews}
              items={c.perspectives.map((p: Json) => ({
                key: p.key,
                label: p.label,
                value: p.value,
                figure: `${p.value} of ${c.assessed_reviews}`,
                tone: p.value ? "warn" : "neutral",
                tip: { title: `${p.label}: ${p.value} concern${p.value === 1 ? "" : "s"}`, lines: [p.content_ids.length ? `On ${p.content_ids.slice(0, 4).join(", ")}${p.content_ids.length > 4 ? "…" : ""}` : "No concerns"] },
              }))}
            />
          )}
        </ChartPanel>
      </BentoGrid>
    </>
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
  const insights = useInsights("content", reviewer);

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
          {reviewer && <ReviewAttention q={insights} />}
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
      {reviewer && <ReviewHistory q={insights} />}
    </>
  );
}
