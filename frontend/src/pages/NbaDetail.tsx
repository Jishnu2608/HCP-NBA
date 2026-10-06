import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUpRight,
  Ban,
  CalendarClock,
  CheckCircle2,
  Gauge,
  MessageSquare,
  PenLine,
  RefreshCw,
  Send,
  ShieldAlert,
  ShieldCheck,
  Target,
  UserRound,
  XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { api, patch, post } from "../api";
import type { Json } from "../api";
import { useToast } from "../toast";
import {
  Alert,
  Badge,
  Button,
  Card,
  ChannelIcon,
  ErrorNote,
  ErrorState,
  Loading,
  MlrBadge,
  PageHeader,
  Segmented,
  SegmentBadge,
  StatusBadge,
  Table,
  TextArea,
  TextField,
  Timeline,
  TimelineItem,
  cx,
  eventLabel,
  fmtDate,
  fmtDateTime,
  pct,
  titleCase,
} from "../ui";

const KIND: Record<string, { label: string; icon: ReactNode }> = {
  who: { label: "Profile signals", icon: <UserRound className="h-4 w-4" aria-hidden /> },
  action: { label: "Why this action", icon: <Target className="h-4 w-4" aria-hidden /> },
  channel: { label: "Why this channel", icon: <MessageSquare className="h-4 w-4" aria-hidden /> },
  timing: { label: "Why now", icon: <CalendarClock className="h-4 w-4" aria-hidden /> },
  compliance: { label: "Compliance and consent", icon: <ShieldCheck className="h-4 w-4" aria-hidden /> },
  withheld: { label: "Held back by a safeguard", icon: <ShieldAlert className="h-4 w-4" aria-hidden /> },
};
const KIND_ORDER = ["who", "action", "channel", "timing", "compliance", "withheld"];

const cap = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

function auditTone(action: string): "neutral" | "brand" | "ok" | "warn" | "bad" {
  if (/block|reject|withdraw/.test(action)) return "bad";
  if (/approv/.test(action)) return "brand";
  if (/sent|respon|outcome|fill/.test(action)) return "ok";
  if (/edit|redraft/.test(action)) return "warn";
  return "neutral";
}

/** Scrolls to and focuses the visible element matching `selector` (layouts render some
 *  panels twice, one per width). */
function reveal(selector: string) {
  const el = [...document.querySelectorAll<HTMLElement>(selector)].find((e) => e.offsetParent !== null);
  if (!el) return;
  el.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  el.querySelector<HTMLElement>("button:not([disabled]), textarea, input")?.focus({ preventScroll: true });
}

function Fact({ icon, label, value, hint }: { icon: ReactNode; label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg bg-subtle px-3.5 py-3">
      <dt className="flex items-center gap-1.5 text-[13px] text-ink-subtle">
        {icon}
        {label}
      </dt>
      <dd className="mt-1 text-[15px] font-semibold leading-6 text-ink">{value}</dd>
      {hint && <dd className="text-xs text-ink-subtle">{hint}</dd>}
    </div>
  );
}

export default function NbaDetail() {
  const { id } = useParams();
  const client = useQueryClient();
  const toast = useToast();
  const detail = useQuery({ queryKey: ["nba-detail", id], queryFn: () => api(`/nba/${id}`) });
  const [draftId, setDraftId] = useState<number | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  const n: Json = detail.data;
  const draft = n?.drafts.find((d: Json) => d.id === draftId);

  useEffect(() => {
    if (!n) return;
    const selected = n.drafts.find((d: Json) => d.is_selected) ?? n.drafts[0];
    if (selected && (draftId === null || !n.drafts.some((d: Json) => d.id === draftId))) {
      setDraftId(selected.id);
    }
  }, [n, draftId]);

  useEffect(() => {
    if (draft) {
      setSubject(draft.subject ?? "");
      setBody(draft.body);
    }
  }, [draft]);

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["nba-detail", id] });
    void client.invalidateQueries({ queryKey: ["nba"] });
    // Approving, sending, recording an outcome or reviewing a response changes the menu count.
    void client.invalidateQueries({ queryKey: ["attention"] });
  };
  const act = useMutation({
    mutationFn: (v: { fn: () => Promise<unknown>; done: string }) => v.fn(),
    onSuccess: (_, v) => {
      toast(v.done);
      setRejecting(false);
      setReason("");
    },
    onSettled: refresh,
  });
  const run = (fn: () => Promise<unknown>, done: string) => act.mutate({ fn, done });

  if (detail.isLoading) return <Loading label="Loading recommendation" />;
  if (detail.error) return <ErrorState error={detail.error} retry={() => void detail.refetch()} variant="page" title="This recommendation could not be loaded" />;

  const dirty = draft && (body !== draft.body || subject !== (draft.subject ?? ""));
  const reviewing = n.can_review && n.status === "ready_for_review";
  const blocked = n.status === "blocked";
  // The safeguards as they stand now (consent, content approval and dates, the medication):
  // shown before anyone acts, not discovered at the approve button.
  const failingNow = Boolean(n.gate_now && !n.gate_now.ok);
  const responseToReview =
    n.status === "responded" && n.can_review && n.target_origin && n.target_origin !== "synthetic" && !n.response_reviewed_ts;
  const humanChannel = n.channel === "phone" || n.channel === "rep_visit";
  const isPatient = n.target_type === "PATIENT";
  const profileLink = n.target_name && (isPatient ? `/patients/${n.target_id}` : `/hcps/${n.target_id}`);
  const grouped = KIND_ORDER.map((kind) => ({
    kind,
    reasons: n.reason_codes.filter((r: Json) => r.kind === kind),
  })).filter((g) => g.reasons.length);

  const regular = grouped.filter((g) => g.kind !== "withheld");

  const saveEdit = () => patch(`/nba/${id}/drafts/${draftId}`, { subject: subject || null, body });
  const approve = async (thenSend: boolean) => {
    if (dirty) await saveEdit();
    await post(`/nba/${id}/approve`, { draft_id: draftId });
    if (thenSend) await post(`/nba/${id}/send`);
  };

  const decision = n.can_review && (
    <Card
      dataAttr="decision"
      title="Decision"
      description={reviewing ? "You are the reviewer for this recommendation." : undefined}
      className={cx(reviewing && "ring-1 ring-primary-line")}
    >
      {reviewing && !rejecting && (
        <div className="flex flex-col gap-2">
          <Button
            variant="primary"
            size="lg"
            busy={act.isPending}
            onClick={() => run(() => approve(true), "Recommendation approved and sent")}
          >
            <Send className="h-4 w-4" aria-hidden /> Approve and send
          </Button>
          <div className="grid grid-cols-2 gap-2">
            <Button busy={act.isPending} onClick={() => run(() => approve(false), "Recommendation approved")}>
              <CheckCircle2 className="h-4 w-4" aria-hidden /> Approve only
            </Button>
            <Button variant="quiet-danger" onClick={() => setRejecting(true)}>
              <XCircle className="h-4 w-4" aria-hidden /> Reject
            </Button>
          </div>
          {dirty && <p className="text-[13px] text-warn">Your edits to the draft are saved when you approve.</p>}
        </div>
      )}
      {reviewing && rejecting && (
        <div className="space-y-3">
          <TextArea
            label="Reason for rejecting"
            hint="Recorded in the audit trail."
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            autoFocus
          />
          <div className="flex flex-wrap gap-2">
            <Button
              variant="danger"
              busy={act.isPending}
              disabled={reason.trim().length < 3}
              onClick={() => run(() => post(`/nba/${id}/reject`, { reason }), "Recommendation rejected")}
            >
              Confirm rejection
            </Button>
            <Button variant="ghost" onClick={() => setRejecting(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {n.status === "approved" && (
        <div className="flex flex-col gap-2">
          <Button variant="primary" size="lg" busy={act.isPending} onClick={() => run(() => post(`/nba/${id}/send`), "Message sent")}>
            <Send className="h-4 w-4" aria-hidden /> Send now
          </Button>
          <Button
            variant="quiet-danger"
            busy={act.isPending}
            onClick={() => run(() => post(`/nba/${id}/reject`, { reason: "Withdrawn after approval" }), "Recommendation withdrawn")}
          >
            Withdraw
          </Button>
        </div>
      )}
      {n.status === "sent" && humanChannel && (
        <div>
          <p className="mb-3 text-sm text-ink-muted">How did the {n.channel_label} go?</p>
          <div className="grid gap-2 sm:grid-cols-3 xl:grid-cols-1">
            {["completed", "no_response", "declined"].map((outcome) => (
              <Button
                key={outcome}
                busy={act.isPending}
                onClick={() => run(() => post(`/nba/${id}/outcome`, { outcome }), "Outcome recorded")}
              >
                {titleCase(outcome)}
              </Button>
            ))}
          </div>
        </div>
      )}
      {n.status === "sent" && !humanChannel && (
        <p className="text-sm text-ink-muted">Sent. The response is captured automatically when the recipient reacts.</p>
      )}
      {n.status === "responded" && n.response && (
        <p className="mb-3 text-sm text-ink-muted">
          {n.target_name ?? "The person"} responded: <span className="font-semibold text-ink">{titleCase(n.response.outcome)}</span>
          {n.response.at ? ` · ${fmtDateTime(n.response.at)}` : ""}
        </p>
      )}
      {responseToReview && (
        <Button variant="primary" busy={act.isPending} onClick={() => run(() => post(`/nba/${id}/response-reviewed`), "Response marked as reviewed")}>
          Mark response reviewed
        </Button>
      )}
      {["responded", "rejected", "expired", "blocked"].includes(n.status) && !responseToReview && (
        <p className="flex items-center gap-2 text-sm text-ink-muted">
          <StatusBadge status={n.status} /> No further action.
        </p>
      )}
      {failingNow && reviewing && (
        <Alert tone="bad" title="A safeguard fails now" className="mt-3">
          {n.gate_now.reason}. Approving will block this recommendation; it cannot be sent.
        </Alert>
      )}
      <ErrorNote error={act.error} className="mt-3" />
      <p className="mt-4 flex gap-2 border-t border-line pt-3 text-[13px] leading-5 text-ink-subtle">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        Content approval, consent and contact limits are checked again at approval and at send.
      </p>
    </Card>
  );

  const content = (
    <Card title="Approved content" description="The only source the message may draw on.">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-ink">{n.content.title}</div>
          <div className="tabular mt-0.5 text-xs text-ink-subtle">
            {n.content.content_id} · version {n.content.version}
          </div>
        </div>
        <MlrBadge status={n.content.mlr_status} expired={n.content.is_expired} />
      </div>
      <p className="mt-3 text-sm leading-6 text-ink-muted">{n.content.body}</p>
      {n.content.expiry_date && (
        <p className="mt-3 text-[13px] text-ink-subtle">Approval valid until {fmtDate(n.content.expiry_date)}</p>
      )}
    </Card>
  );

  return (
    <>
      <PageHeader
        back
        title={n.target_name ?? `${isPatient ? "Patient" : "HCP"} · identity hidden`}
        meta={
          <div className="flex flex-wrap items-center gap-1.5">
            <SegmentBadge value={n.segment} />
            <StatusBadge status={n.status} />
          </div>
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>{isPatient ? "Patient recommendation" : "HCP recommendation"}</span>
            {profileLink && (
              <Link to={profileLink} className="inline-flex items-center gap-1 font-semibold text-primary-ink hover:underline">
                Open {isPatient ? "Patient" : "HCP"} 360 <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
              </Link>
            )}
          </span>
        }
      />

      {blocked && (
        <Alert tone="bad" title="Blocked by a safeguard. This recommendation cannot be sent." className="mb-6">
          <span className="text-bad">{n.block_reason}</span>
        </Alert>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-6">
          {/* The recommendation, in reading order: action, why, channel, timing,
              compliance, expected outcome, then the primary action. */}
          <section
            aria-labelledby="nba-hero"
            className={cx(
              "overflow-hidden rounded-xl border bg-surface shadow-card",
              blocked ? "border-bad-line" : "border-primary-line",
            )}
          >
            <div className={cx("h-1", blocked ? "bg-bad-fill" : "bg-primary")} aria-hidden />
            <div className="px-5 pb-6 pt-5 sm:px-6">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-[13px] font-semibold text-ink-subtle">
                  <span className={cx("h-2 w-2 rounded-full", blocked ? "bg-bad-fill" : "bg-primary")} aria-hidden />
                  Next best action
                </span>
                <span className="tabular text-[13px] text-ink-subtle">Priority {n.priority.toFixed(1)}</span>
              </div>
              <h2
                id="nba-hero"
                className={cx(
                  "mt-1.5 text-[26px] font-semibold leading-tight tracking-[-0.015em] sm:text-[30px]",
                  blocked ? "text-ink-muted line-through decoration-bad/40 decoration-2" : "text-ink",
                )}
              >
                {cap(n.action_label)}
              </h2>
              {(n.rationale_summary || n.block_reason) && (
                <div className="mt-3 max-w-3xl">
                  <div className="text-[13px] font-semibold text-ink-muted">Why this action?</div>
                  <p className={cx("mt-0.5 text-[15px] leading-6", blocked ? "text-bad" : "text-ink")}>
                    {blocked ? n.block_reason : n.rationale_summary}
                  </p>
                </div>
              )}
              <dl className="mt-5 grid grid-cols-2 gap-2.5 lg:grid-cols-4">
                <Fact
                  icon={<ChannelIcon channel={n.channel} className="h-3.5 w-3.5" />}
                  label="Channel"
                  value={cap(n.channel_label)}
                />
                <Fact icon={<CalendarClock className="h-3.5 w-3.5" aria-hidden />} label="Timing" value={n.timing_note} />
                <Fact
                  icon={blocked || failingNow ? <Ban className="h-3.5 w-3.5" aria-hidden /> : <ShieldCheck className="h-3.5 w-3.5" aria-hidden />}
                  label="Compliance"
                  value={
                    <span className={blocked || failingNow ? "text-bad" : "text-ok"}>
                      {blocked ? "Blocked by a safeguard" : failingNow ? "Fails a safeguard now" : n.gate_now ? "All safeguards pass now" : "All safeguards passed"}
                    </span>
                  }
                  hint={failingNow ? n.gate_now.reason : n.content.is_expired ? "Content approval expired" : `MLR ${n.content.mlr_status}`}
                />
                {isPatient ? (
                  <Fact
                    icon={<Gauge className="h-3.5 w-3.5" aria-hidden />}
                    label="Chance of a refill"
                    value={<span className="tabular">{pct(n.predicted?.p_outcome)}</span>}
                    hint={`Response ${pct(n.predicted?.p_engage)}`}
                  />
                ) : (
                  <Fact
                    icon={<Gauge className="h-3.5 w-3.5" aria-hidden />}
                    label="Chance of a response"
                    value={<span className="tabular">{pct(n.predicted?.p_engage)}</span>}
                  />
                )}
              </dl>
              {n.can_review && (reviewing || n.status === "approved") && (
                <div className="mt-5 flex flex-wrap gap-2">
                  <Button variant="primary" onClick={() => reveal("[data-decision]")}>
                    {reviewing ? "Review and decide" : "Send or withdraw"}
                    <ArrowDown className="h-4 w-4" aria-hidden />
                  </Button>
                  {reviewing && n.drafts.length > 0 && (
                    <Button variant="ghost" onClick={() => reveal("[data-draft]")}>
                      <PenLine className="h-4 w-4" aria-hidden /> Edit message
                    </Button>
                  )}
                </div>
              )}
            </div>
          </section>

          {/* The rationale as a bento of evidence: one tile per kind of reason. */}
          <section aria-labelledby="nba-why">
            <h2 id="nba-why" className="mb-3 text-[15px] font-semibold text-ink">
              Why this recommendation
            </h2>
            <div className="stagger grid gap-4 md:grid-cols-2">
              {grouped.map((g) => (
                <div
                  key={g.kind}
                  className={cx(
                    "flex min-w-0 gap-3 rounded-xl border bg-surface p-4 shadow-card sm:p-5",
                    g.kind === "withheld" ? "border-warn-line md:col-span-2" : "border-line",
                    // An odd number of tiles: the last regular tile takes the full row, so no cell is empty.
                    g.kind !== "withheld" && regular.length % 2 === 1 && g === regular[regular.length - 1] && "md:col-span-2",
                  )}
                >
                  <span
                    className={cx(
                      "grid h-8 w-8 shrink-0 place-items-center rounded-lg",
                      g.kind === "withheld" ? "bg-warn-soft text-warn" : "bg-primary-soft text-primary-ink",
                    )}
                  >
                    {KIND[g.kind].icon}
                  </span>
                  <div className="min-w-0">
                    <h3 className="text-[13px] font-semibold text-ink-muted">{KIND[g.kind].label}</h3>
                    <ul className="mt-1 space-y-1 text-sm leading-6 text-ink">
                      {g.reasons.map((r: Json, i: number) => (
                        <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                          <span>{r.text}</span>
                          {r.points !== undefined && (
                            <span className="tabular rounded bg-sunken px-1 text-xs font-medium text-ink-muted">
                              +{r.points} risk pts
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {n.drafts.length > 0 && (
            <Card
              dataAttr="draft"
              title="Message draft"
              description={reviewing ? "Edit the wording if needed. Eligibility is already decided above." : undefined}
              action={
                reviewing && (
                  <Button
                    variant="ghost"
                    size="sm"
                    busy={act.isPending}
                    onClick={() => run(() => post(`/nba/${id}/redraft`), "New drafts generated")}
                  >
                    <RefreshCw className="h-4 w-4" aria-hidden /> Regenerate
                  </Button>
                )
              }
            >
              {n.drafts.length > 1 && (
                <div className="mb-4">
                  <Segmented
                    label="Draft variant"
                    value={String(draftId)}
                    onChange={(v) => setDraftId(Number(v))}
                    options={n.drafts.map((d: Json) => ({
                      value: String(d.id),
                      label: `Variant ${d.variant_no}${d.edited ? " · edited" : ""}`,
                    }))}
                  />
                </div>
              )}
              {draft && (
                <div className="space-y-4">
                  {n.channel !== "sms" && (
                    <TextField
                      label="Subject"
                      value={subject}
                      onChange={(e) => setSubject(e.target.value)}
                      readOnly={!reviewing}
                    />
                  )}
                  <TextArea
                    label="Message"
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    readOnly={!reviewing}
                    rows={humanChannel ? 9 : 7}
                  />
                  <div className="flex flex-wrap items-center justify-between gap-2 text-[13px] text-ink-subtle">
                    <span className="flex items-center gap-1.5">
                      <PenLine className="h-3.5 w-3.5 shrink-0" aria-hidden />
                      Worded by {draft.provider}
                      {draft.model ? ` (${draft.model})` : ""} from the approved content; checked before it is stored.
                    </span>
                    {n.channel === "sms" && (
                      <span className={cx("tabular font-medium", body.length > 320 && "text-bad")}>{body.length} / 320</span>
                    )}
                  </div>
                  {reviewing && dirty && (
                    <Button busy={act.isPending} onClick={() => run(saveEdit, "Draft saved")}>
                      Save edit
                    </Button>
                  )}
                </div>
              )}
            </Card>
          )}

          <div className="space-y-6 xl:hidden">
            {decision}
            {content}
          </div>

          <Card title="Options the engine considered" description="Models rank the options. Safeguards decide which are allowed.">
            <Table
              caption="Options considered"
              head={["#", "Action", "Channel", "Content", "Score", "Safeguard result"]}
              align={[undefined, undefined, undefined, undefined, "right", undefined]}
            >
              {n.candidates.map((c: Json) => (
                <tr key={c.rank} className={cx(c.chosen && "bg-primary-soft/60")}>
                  <td className="tabular text-ink-subtle">{c.rank}</td>
                  <td className="min-w-40">
                    <span className="font-medium text-ink">{titleCase(c.action_label)}</span>
                    {c.chosen && (
                      <Badge tone="brand" className="ml-2">
                        Chosen
                      </Badge>
                    )}
                  </td>
                  <td className="whitespace-nowrap text-ink-muted">{titleCase(c.channel_label)}</td>
                  <td className="min-w-48 text-ink-muted">
                    <span className="tabular text-xs text-ink-subtle">{c.content_id}</span> {c.content_title}
                  </td>
                  <td className="tabular text-right">{c.score?.toFixed(1)}</td>
                  <td className="min-w-44">
                    {c.eligible ? (
                      <Badge tone="ok" icon={<CheckCircle2 className="h-3.5 w-3.5" aria-hidden />}>
                        Passed
                      </Badge>
                    ) : (
                      <span className="flex items-start gap-1.5 text-[13px] leading-5 text-bad">
                        <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                        {c.gate_text}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </Table>
            <p className="mt-3 text-[13px] text-ink-subtle">A blocked option is never chosen, whatever its score.</p>
          </Card>

          <Card title="Audit trail">
            {n.audit.length ? (
              <Timeline>
                {n.audit.map((a: Json) => (
                  <TimelineItem
                    key={a.id}
                    tone={auditTone(a.action)}
                    title={
                      <>
                        <span className="font-semibold">{eventLabel(a.action)}</span>
                        <span className="text-ink-muted"> by {a.actor}</span>{" "}
                        <span className="text-ink-subtle">({titleCase(a.actor_role)})</span>
                      </>
                    }
                    meta={
                      <>
                        {fmtDateTime(a.ts)}
                        {a.reason ? ` · ${a.reason}` : ""}
                      </>
                    }
                  />
                ))}
              </Timeline>
            ) : (
              <p className="text-sm text-ink-subtle">No events recorded yet.</p>
            )}
          </Card>
        </div>

        <aside className="hidden xl:block" aria-label="Review">
          {/* Sticky while reading the rationale; scrolls itself if taller than the window (high zoom). */}
          <div className="scroll-quiet sticky top-24 max-h-[calc(100dvh-7rem)] space-y-6 overflow-y-auto pb-1">

            {decision}
            {content}
          </div>
        </aside>
      </div>
    </>
  );
}
