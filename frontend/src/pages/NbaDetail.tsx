import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CalendarClock,
  MessageSquare,
  RefreshCw,
  Send,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Target,
  UserRound,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, patch, post } from "../api";
import type { Json } from "../api";
import {
  Badge,
  Button,
  Card,
  ErrorNote,
  Loading,
  MlrBadge,
  SegmentBadge,
  StatusBadge,
  Table,
  cx,
  eventLabel,
  fmtDate,
  fmtDateTime,
  pct,
  titleCase,
} from "../ui";

const KIND: Record<string, { label: string; icon: ReactNode }> = {
  who: { label: "Why this person", icon: <UserRound className="h-4 w-4" /> },
  action: { label: "Why this action", icon: <Target className="h-4 w-4" /> },
  channel: { label: "Why this channel", icon: <MessageSquare className="h-4 w-4" /> },
  timing: { label: "Why now", icon: <CalendarClock className="h-4 w-4" /> },
  compliance: { label: "Compliance and consent", icon: <ShieldCheck className="h-4 w-4" /> },
  withheld: { label: "Held back by a gate", icon: <ShieldAlert className="h-4 w-4" /> },
};
const KIND_ORDER = ["who", "action", "channel", "timing", "compliance", "withheld"];

export default function NbaDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
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
  };
  const act = useMutation({
    mutationFn: (fn: () => Promise<unknown>) => fn(),
    onSettled: refresh,
  });
  const run = (fn: () => Promise<unknown>) => act.mutate(fn);

  if (detail.isLoading) return <Loading />;
  if (detail.error) return <ErrorNote error={detail.error} />;

  const dirty = draft && (body !== draft.body || subject !== (draft.subject ?? ""));
  const reviewing = n.can_review && n.status === "ready_for_review";
  const humanChannel = n.channel === "phone" || n.channel === "rep_visit";
  const profileLink =
    n.target_name && (n.target_type === "PATIENT" ? `/patients/${n.target_id}` : `/hcps/${n.target_id}`);
  const grouped = KIND_ORDER.map((kind) => ({
    kind,
    reasons: n.reason_codes.filter((r: Json) => r.kind === kind),
  })).filter((g) => g.reasons.length);

  const saveEdit = () => patch(`/nba/${id}/drafts/${draftId}`, { subject: subject || null, body });
  const approve = async (thenSend: boolean) => {
    if (dirty) await saveEdit();
    await post(`/nba/${id}/approve`, { draft_id: draftId });
    if (thenSend) await post(`/nba/${id}/send`);
  };

  return (
    <>
      <button
        onClick={() => navigate(-1)}
        className="mb-3 flex items-center gap-1 text-sm text-stone-500 hover:text-stone-800"
      >
        <ArrowLeft className="h-4 w-4" /> Back
      </button>

      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight text-stone-900">
              {n.target_name ?? "Identity hidden for this role"}
            </h1>
            <SegmentBadge value={n.segment} />
            <StatusBadge status={n.status} />
          </div>
          <p className="mt-1 text-sm text-stone-600">
            Recommended: <span className="font-medium text-stone-900">{n.action_label}</span> by{" "}
            <span className="font-medium text-stone-900">{n.channel_label}</span>
            {" · "}
            {n.timing_note}
          </p>
          {profileLink && (
            <Link to={profileLink} className="mt-1 inline-block text-sm text-brand-700 hover:underline">
              Open 360 profile ({n.target_id})
            </Link>
          )}
        </div>
        <div className="flex gap-6 text-right">
          {n.target_type === "PATIENT" && (
            <Metric label="Chance of a fill" value={pct(n.predicted?.p_outcome)} />
          )}
          <Metric label="Chance of a response" value={pct(n.predicted?.p_engage)} />
          <Metric label="Priority" value={n.priority.toFixed(1)} />
        </div>
      </div>

      {n.status === "blocked" && (
        <div className="mb-5 flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <div className="font-semibold">Blocked by a hard gate. This cannot be sent.</div>
            <div>{n.block_reason}</div>
          </div>
        </div>
      )}
      <div className="mb-4">
        <ErrorNote error={act.error} />
      </div>

      <div className="grid gap-5 xl:grid-cols-5">
        <div className="space-y-5 xl:col-span-3">
          <Card title="Why this recommendation">
            <div className="space-y-4">
              {grouped.map((g) => (
                <div key={g.kind} className="flex gap-3">
                  <div
                    className={cx(
                      "mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg",
                      g.kind === "withheld" ? "bg-amber-50 text-amber-700" : "bg-brand-50 text-brand-700",
                    )}
                  >
                    {KIND[g.kind].icon}
                  </div>
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-wide text-stone-500">
                      {KIND[g.kind].label}
                    </div>
                    <ul className="mt-1 space-y-1 text-sm text-stone-800">
                      {g.reasons.map((r: Json, i: number) => (
                        <li key={i} className="flex items-baseline gap-2">
                          <span>{r.text}</span>
                          {r.points !== undefined && (
                            <span className="tabular shrink-0 text-xs text-stone-400">
                              +{r.points} risk points
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card title="Options the engine considered">
            <Table head={["#", "Action", "Channel", "Content", "Score", "Gate result"]}>
              {n.candidates.map((c: Json) => (
                <tr key={c.rank} className={cx(c.chosen && "bg-brand-50/60")}>
                  <td className="tabular px-3 py-2 text-stone-400">{c.rank}</td>
                  <td className="px-3 py-2">
                    {titleCase(c.action_label)}
                    {c.chosen && (
                      <span className="ml-2">
                        <Badge tone="brand">Chosen</Badge>
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">{titleCase(c.channel_label)}</td>
                  <td className="px-3 py-2 text-stone-600">
                    <span className="text-xs text-stone-400">{c.content_id}</span> {c.content_title}
                  </td>
                  <td className="tabular px-3 py-2">{c.score?.toFixed(1)}</td>
                  <td className="px-3 py-2">
                    {c.eligible ? (
                      <Badge tone="good">Passed</Badge>
                    ) : (
                      <span className="text-xs text-red-700">{c.gate_text}</span>
                    )}
                  </td>
                </tr>
              ))}
            </Table>
            <p className="mt-3 text-xs text-stone-500">
              Models rank the options. Gates decide which options are allowed. A gated option is
              never chosen, whatever its score.
            </p>
          </Card>

          <Card title="Audit trail">
            <ol className="space-y-3">
              {n.audit.map((a: Json) => (
                <li key={a.id} className="flex gap-3 text-sm">
                  <div className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand-500" />
                  <div>
                    <div className="text-stone-800">
                      <span className="font-medium">{eventLabel(a.action)}</span> by {a.actor}{" "}
                      <span className="text-stone-400">({titleCase(a.actor_role)})</span>
                    </div>
                    <div className="text-xs text-stone-500">
                      {fmtDateTime(a.ts)}
                      {a.reason ? ` · ${a.reason}` : ""}
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          </Card>
        </div>

        <div className="space-y-5 xl:col-span-2">
          <Card title="Approved content">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-medium text-stone-900">{n.content.title}</div>
                <div className="text-xs text-stone-400">
                  {n.content.content_id} · version {n.content.version}
                </div>
              </div>
              <MlrBadge status={n.content.mlr_status} expired={n.content.is_expired} />
            </div>
            <p className="mt-2 text-sm text-stone-600">{n.content.body}</p>
            {n.content.expiry_date && (
              <p className="mt-2 text-xs text-stone-500">
                Approval valid until {fmtDate(n.content.expiry_date)}
              </p>
            )}
          </Card>

          {n.drafts.length > 0 && (
            <Card
              title="Message draft"
              action={
                reviewing && (
                  <Button
                    variant="ghost"
                    busy={act.isPending}
                    onClick={() => run(() => post(`/nba/${id}/redraft`))}
                  >
                    <RefreshCw className="h-4 w-4" /> Regenerate
                  </Button>
                )
              }
            >
              <div className="mb-3 flex flex-wrap gap-1.5">
                {n.drafts.map((d: Json) => (
                  <button
                    key={d.id}
                    onClick={() => setDraftId(d.id)}
                    className={cx(
                      "rounded-lg px-2.5 py-1 text-xs font-medium ring-1 ring-inset",
                      d.id === draftId
                        ? "bg-brand-600 text-white ring-brand-600"
                        : "bg-white text-stone-600 ring-stone-200 hover:bg-stone-50",
                    )}
                  >
                    Variant {d.variant_no}
                    {d.edited ? " (edited)" : ""}
                  </button>
                ))}
              </div>
              {draft && (
                <>
                  {n.channel !== "sms" && (
                    <input
                      value={subject}
                      onChange={(e) => setSubject(e.target.value)}
                      readOnly={!reviewing}
                      placeholder="Subject"
                      className="mb-2 w-full rounded-lg border border-stone-300 px-3 py-2 text-sm font-medium read-only:bg-stone-50"
                    />
                  )}
                  <textarea
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    readOnly={!reviewing}
                    rows={humanChannel ? 9 : 7}
                    className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm leading-relaxed read-only:bg-stone-50"
                  />
                  <div className="mt-1.5 flex items-center justify-between text-xs text-stone-500">
                    <span className="flex items-center gap-1">
                      <Sparkles className="h-3.5 w-3.5" />
                      Drafted by {draft.provider}
                      {draft.model ? ` (${draft.model})` : ""}; wording is checked before it is stored
                    </span>
                    {n.channel === "sms" && <span className="tabular">{body.length} / 320</span>}
                  </div>
                  {reviewing && dirty && (
                    <Button className="mt-3" busy={act.isPending} onClick={() => run(saveEdit)}>
                      Save edit
                    </Button>
                  )}
                </>
              )}
            </Card>
          )}

          {n.can_review && (
            <Card title="Decision">
              {reviewing && !rejecting && (
                <div className="flex flex-wrap gap-2">
                  <Button variant="primary" busy={act.isPending} onClick={() => run(() => approve(true))}>
                    <Send className="h-4 w-4" /> Approve and send
                  </Button>
                  <Button busy={act.isPending} onClick={() => run(() => approve(false))}>
                    Approve only
                  </Button>
                  <Button variant="danger" onClick={() => setRejecting(true)}>
                    Reject
                  </Button>
                </div>
              )}
              {reviewing && rejecting && (
                <div>
                  <textarea
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    rows={2}
                    placeholder="Reason for rejecting (recorded in the audit trail)"
                    className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
                  />
                  <div className="mt-2 flex gap-2">
                    <Button
                      variant="danger"
                      busy={act.isPending}
                      disabled={reason.trim().length < 3}
                      onClick={() => run(() => post(`/nba/${id}/reject`, { reason }))}
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
                <div className="flex flex-wrap gap-2">
                  <Button variant="primary" busy={act.isPending} onClick={() => run(() => post(`/nba/${id}/send`))}>
                    <Send className="h-4 w-4" /> Send now
                  </Button>
                  <Button
                    variant="danger"
                    busy={act.isPending}
                    onClick={() => run(() => post(`/nba/${id}/reject`, { reason: "Withdrawn after approval" }))}
                  >
                    Withdraw
                  </Button>
                </div>
              )}
              {n.status === "sent" && humanChannel && (
                <div>
                  <p className="mb-2 text-sm text-stone-600">How did the {n.channel_label} go?</p>
                  <div className="flex flex-wrap gap-2">
                    {["completed", "no_response", "declined"].map((outcome) => (
                      <Button
                        key={outcome}
                        busy={act.isPending}
                        onClick={() => run(() => post(`/nba/${id}/outcome`, { outcome }))}
                      >
                        {titleCase(outcome)}
                      </Button>
                    ))}
                  </div>
                </div>
              )}
              {n.status === "sent" && !humanChannel && (
                <p className="text-sm text-stone-600">
                  Sent. The response is captured automatically when the recipient reacts.
                </p>
              )}
              {["responded", "rejected", "expired", "blocked"].includes(n.status) && (
                <p className="text-sm text-stone-600">No further action: {titleCase(n.status)}.</p>
              )}
              <p className="mt-3 text-xs text-stone-500">
                MLR status, consent and contact limits are checked again at approval and at send.
              </p>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="tabular text-xl font-semibold text-stone-900">{value}</div>
      <div className="text-xs text-stone-500">{label}</div>
    </div>
  );
}
