import { useQuery } from "@tanstack/react-query";
import { Ban, CheckCircle2, Clock3, Send, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, query } from "../api";
import type { Json } from "../api";
import { useAttention } from "../attention";
import { useAuth } from "../auth";
import { P } from "../permissions";
import { OriginBadge } from "./Care";
import {
  Alert,
  Badge,
  Card,
  ChannelIcon,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingRows,
  Meter,
  PageHeader,
  Pagination,
  Segmented,
  SegmentBadge,
  Select,
  KpiGrid,
  Stat,
  StatusBadge,
  Toolbar,
  cx,
} from "../ui";
import type { Column } from "../ui";

const PAGE = 25;
const TITLE: Record<string, [string, string]> = {
  patients: [
    "Adherence queue",
    "Patients who need outreach now, ranked by adherence risk and the chance the action leads to a refill.",
  ],
  hcps: ["HCP queue", "The next best action for each assigned HCP, ranked by value and predicted engagement."],
  gated: [
    "Blocked and held-back recommendations",
    "Recommendations a safeguard stopped, or where a better option was held back. Identities are hidden for this role.",
  ],
  all: ["All recommendations", "Every recommendation across patients and HCPs, ranked by priority."],
};

type FilterKey = "open" | "ready_for_review" | "approved" | "sent" | "responses" | "blocked" | "closed";
const FILTERS: Array<{ key: FilterKey; label: string; statuses: string[] }> = [
  { key: "open", label: "Open", statuses: ["ready_for_review", "approved", "blocked"] },
  { key: "ready_for_review", label: "Pending review", statuses: ["ready_for_review"] },
  { key: "approved", label: "Approved", statuses: ["approved"] },
  { key: "sent", label: "Sent", statuses: ["sent", "responded"] },
  // Registered patients answered and nobody on the care team has looked yet.
  { key: "responses", label: "Responses to review", statuses: ["responded"] },
  { key: "blocked", label: "Blocked", statuses: ["blocked"] },
  { key: "closed", label: "Rejected or superseded", statuses: ["rejected", "expired"] },
];

const cap = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

export default function Queue() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<FilterKey>("open");
  const [audience, setAudience] = useState("");
  const [realOnly, setRealOnly] = useState(false);
  const [page, setPage] = useState(0);
  const statuses = FILTERS.find((f) => f.key === filter)!.statuses;
  const list = useQuery({
    queryKey: ["nba", filter, audience, realOnly, page],
    queryFn: () =>
      api(
        `/nba${query({ status: statuses, target_type: audience, real_only: realOnly ? "true" : "", responses: filter === "responses" ? "true" : "", limit: PAGE, offset: page * PAGE })}`,
      ),
    placeholderData: (previous) => previous,
  });
  const attention = useAttention();
  const careRequests = can(P.PATIENT_CARE_MANAGE) ? attention.care_requests ?? 0 : 0;
  const realWaiting: number = list.data?.real_waiting ?? 0;
  const work: Record<string, number> | null = list.data?.work ?? null;
  const responses = work?.responses ?? 0;
  const callOutcomes = work?.call_outcomes ?? 0;
  const pick = (key: FilterKey) => {
    setFilter(key);
    setRealOnly(false);
    setPage(0);
  };
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
  const shown = (n: number) => (list.data ? n.toLocaleString() : "—");
  // Priority is risk (or value) times the predicted chance of success; bars are relative
  // to the highest priority on the page.
  const maxPriority = Math.max(1, ...((list.data?.items ?? []) as Json[]).map((n) => n.priority));
  const audienceWord = view === "hcps" ? "HCP" : view === "patients" ? "patient" : null;

  const columns: Column<Json>[] = [
    {
      key: "who",
      header: "Who",
      primary: true,
      className: "w-[24%] min-w-48",
      cell: (n) => (
        <div className="min-w-0">
          <Link
            to={`/nba/${n.id}`}
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
            className="block font-semibold text-ink [overflow-wrap:anywhere] hover:text-primary-ink hover:underline"
            title={n.target_name ?? undefined}
          >
            {n.target_name ?? `${n.target_type === "HCP" ? "HCP" : "Patient"} · identity hidden`}
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {n.target_name && <span className="tabular text-xs text-ink-subtle">{n.target_id}</span>}
            <OriginBadge origin={n.target_origin} />
            <SegmentBadge value={n.segment} />
          </div>
        </div>
      ),
    },
    {
      key: "action",
      header: "Recommended action",
      className: "min-w-52",
      cell: (n) => (
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 font-medium text-ink">
            <ChannelIcon channel={n.channel} className="h-4 w-4 shrink-0 text-ink-subtle" />
            <span>
              {cap(n.action_label)} <span className="font-normal text-ink-muted">by {n.channel_label}</span>
            </span>
          </div>
          <div className="mt-0.5 line-clamp-1 text-[13px] text-ink-subtle" title={n.content_title}>
            {n.content_title}
          </div>
        </div>
      ),
    },
    {
      key: "why",
      header: "Why",
      className: "min-w-64",
      hideOnMobile: false,
      cell: (n) => (
        <div className="max-w-md">
          {n.status === "blocked" ? (
            <span className="flex items-start gap-1.5 text-[13px] leading-5 text-bad">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              {n.block_reason}
            </span>
          ) : (
            <span className="line-clamp-2 text-[13px] leading-5 text-ink-muted" title={n.rationale_summary ?? n.timing_note}>
              {n.rationale_summary ?? n.timing_note}
            </span>
          )}
          {n.gate_now && !n.gate_now.ok && (
            <div className="mt-1.5 flex items-start gap-1.5 text-[13px] leading-5 text-bad">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              Safeguard fails now: {n.gate_now.reason}
            </div>
          )}
          {n.status === "responded" && n.target_origin && n.target_origin !== "synthetic" && !n.response_reviewed_ts && (
            <div className="mt-1.5">
              <Badge tone="info">Response to review</Badge>
            </div>
          )}
          {n.has_withheld && n.status !== "blocked" && (
            <div className="mt-1.5">
              <Badge tone="warn" icon={<ShieldAlert className="h-3.5 w-3.5" aria-hidden />}>
                Better option held back by a safeguard
              </Badge>
            </div>
          )}
        </div>
      ),
    },
    {
      key: "priority",
      header: "Priority",
      align: "right",
      className: "w-32",
      cell: (n) => (
        <div className="ml-auto flex w-24 items-center gap-2 md:w-28">
          <Meter value={n.priority / maxPriority} tone={n.status === "blocked" ? "neutral" : "brand"} label="Priority" className="flex-1" />
          <span className="tabular w-9 text-right text-[13px] font-semibold text-ink">{n.priority.toFixed(1)}</span>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      className: "w-36",
      hideOnMobile: true,
      cell: (n) => <StatusBadge status={n.status} />,
    },
  ];

  return (
    <>
      <PageHeader title={title} subtitle={subtitle} />
      {(careRequests > 0 ||
        (responses > 0 && filter !== "responses") ||
        (callOutcomes > 0 && filter !== "sent") ||
        (realWaiting > 0 && !realOnly)) && (
        <Alert tone="warn" title="Your patients need you" className="mb-6">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            {careRequests > 0 && (
              <Link to="/care" className="font-semibold text-primary-ink underline">
                {careRequests} care request{careRequests === 1 ? "" : "s"} to handle
              </Link>
            )}
            {responses > 0 && filter !== "responses" && (
              <button type="button" className="font-semibold text-primary-ink underline" onClick={() => pick("responses")}>
                {responses} patient response{responses === 1 ? "" : "s"} to review
              </button>
            )}
            {callOutcomes > 0 && filter !== "sent" && (
              <button type="button" className="font-semibold text-primary-ink underline" onClick={() => pick("sent")}>
                {callOutcomes} call outcome{callOutcomes === 1 ? "" : "s"} to record
              </button>
            )}
            {realWaiting > 0 && !realOnly && (
              <button
                type="button"
                className="font-semibold text-primary-ink underline"
                onClick={() => {
                  setRealOnly(true);
                  setFilter("ready_for_review");
                  setPage(0);
                }}
              >
                {realWaiting} recommendation{realWaiting === 1 ? "" : "s"} for registered patients
              </button>
            )}
          </div>
        </Alert>
      )}

      <KpiGrid>
        <Stat
          label="Pending review"
          value={shown(countFor(["ready_for_review"]))}
          hint={
            audienceWord
              ? `${audienceWord} actions waiting for you`
              : view === "gated"
                ? "with the care team or representative"
                : "waiting for a reviewer"
          }
          icon={<Clock3 className="h-4 w-4" aria-hidden />}
          tone="brand"
        />
        <Stat
          label="Approved, not sent"
          value={shown(countFor(["approved"]))}
          hint={view === "gated" ? "sending re-checks every safeguard" : "ready to send"}
          icon={<CheckCircle2 className="h-4 w-4" aria-hidden />}
        />
        <Stat
          label="Blocked"
          value={shown(countFor(["blocked"]))}
          hint="a safeguard stopped these"
          icon={<Ban className="h-4 w-4" aria-hidden />}
          tone={countFor(["blocked"]) ? "bad" : undefined}
        />
        <Stat
          label="Sent"
          value={shown(countFor(["sent", "responded"]))}
          hint={`${(counts.responded ?? 0).toLocaleString()} responded`}
          icon={<Send className="h-4 w-4" aria-hidden />}
        />
      </KpiGrid>

      <Toolbar>
        <Segmented
          label="Filter by status"
          value={filter}
          onChange={(v) => {
            setFilter(v);
            setPage(0);
          }}
          options={FILTERS.filter((f) => f.key !== "responses" || work).map((f) => ({
            value: f.key,
            label: f.label,
            count: f.key === "responses" ? responses : countFor(f.statuses),
          }))}
        />
        {seesAll && (
          <Select
            label="Audience"
            value={audience}
            onChange={(e) => {
              setAudience(e.target.value);
              setPage(0);
            }}
            className="w-full sm:w-52"
          >
            <option value="">Patients and HCPs</option>
            <option value="PATIENT">Patients only</option>
            <option value="HCP">HCPs only</option>
          </Select>
        )}
        {(can(P.NBA_READ_ALL) || can(P.NBA_READ_PATIENT_ASSIGNED)) && (
          <label className="flex min-h-11 items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              className="h-4 w-4"
              aria-label="Registered patients only"
              checked={realOnly}
              onChange={(e) => {
                setRealOnly(e.target.checked);
                setPage(0);
              }}
            />
            Registered patients only
          </label>
        )}
      </Toolbar>

      <Card flush className={cx(list.isFetching && !list.isLoading && "opacity-70 transition-opacity")}>
        {list.isLoading ? (
          <div className="p-5">
            <LoadingRows rows={8} label="Loading recommendations" />
          </div>
        ) : list.error ? (
          <ErrorState error={list.error} retry={() => void list.refetch()} title="Recommendations could not be loaded" />
        ) : !list.data.items.length ? (
          <EmptyState title="Nothing in this view" icon={<CheckCircle2 className="h-5 w-5" />}>
            {filter === "ready_for_review"
              ? "No recommendations are waiting for review. New ones appear after the next engine cycle."
              : "Try another status filter."}
          </EmptyState>
        ) : (
          <>
            <DataTable
              caption={title}
              tableFrom="4xl"
              columns={columns}
              rows={list.data.items}
              rowKey={(n) => n.id}
              onRowClick={(n) => navigate(`/nba/${n.id}`)}
              rowClassName={(n) => n.status === "blocked" && "bg-bad-soft/35"}
              mobileAside={(n) => <StatusBadge status={n.status} />}
            />
            <Pagination page={page} pageSize={PAGE} total={list.data.total} onPage={setPage} />
          </>
        )}
      </Card>
    </>
  );
}
