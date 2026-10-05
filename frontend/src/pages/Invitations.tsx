// Invitations: how professional accounts are created. Administrators see every invitation
// and may invite any professional role; an HCP sees only the invitations they sent and may
// invite representatives and care managers. The role choices come from the server, and the
// server checks every action again: hiding an option here is presentation, not security.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CheckCircle2, Clock3, Copy, MailCheck, MailPlus, MailX, RefreshCw, Send, TerminalSquare, UserPlus } from "lucide-react";
import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { api, post, query } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { P } from "../permissions";
import { useToast } from "../toast";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorNote,
  ErrorState,
  KpiGrid,
  LoadingRows,
  PageHeader,
  PersonName,
  Segmented,
  Stat,
  TextField,
  fmtDateTime,
  num,
} from "../ui";
import type { Column, Tone } from "../ui";
import { SpecialtyPicker, specialtyText } from "./HealthForms";

interface RoleOption {
  role: string;
  label: string;
  description: string;
}

const STATUS: Record<string, { tone: Tone; label: string; icon: ReactNode }> = {
  pending: { tone: "warn", label: "Pending", icon: <Clock3 className="h-3.5 w-3.5" aria-hidden /> },
  accepted: { tone: "ok", label: "Accepted", icon: <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> },
  expired: { tone: "neutral", label: "Expired", icon: <Clock3 className="h-3.5 w-3.5" aria-hidden /> },
  revoked: { tone: "bad", label: "Revoked", icon: <Ban className="h-3.5 w-3.5" aria-hidden /> },
};

function InvitationStatus({ status }: { status: string }) {
  const s = STATUS[status] ?? { tone: "neutral" as Tone, label: status, icon: null };
  return (
    <Badge tone={s.tone} icon={s.icon}>
      {s.label}
    </Badge>
  );
}

function Delivery({ value }: { value: string }) {
  if (value === "failed")
    return (
      <span className="inline-flex items-center gap-1 text-[13px] font-medium text-bad">
        <MailX className="h-3.5 w-3.5" aria-hidden /> Not sent
      </span>
    );
  if (value === "development")
    return (
      <span className="inline-flex items-center gap-1 text-[13px] text-warn">
        <TerminalSquare className="h-3.5 w-3.5" aria-hidden /> No email (development)
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 text-[13px] text-ink-muted">
      <MailCheck className="h-3.5 w-3.5" aria-hidden /> Emailed
    </span>
  );
}

/** Development only (no mail server): the link is shown to the inviter, clearly labelled. */
function DevLink({ link, onClose }: { link: string; onClose: () => void }) {
  const toast = useToast();
  return (
    <Alert
      tone="warn"
      icon={<TerminalSquare className="h-5 w-5" aria-hidden />}
      title="Development mode: no email was sent"
      className="mb-6"
      action={
        <Button size="sm" variant="ghost" onClick={onClose}>
          Dismiss
        </Button>
      }
    >
      No mail server is configured, so the invitation link is shown here instead. With email configured it goes only
      to the invited address.
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 break-all rounded-lg border border-warn-line bg-surface px-3 py-2 text-xs text-ink">
          {link}
        </code>
        <Button
          size="sm"
          onClick={() => void navigator.clipboard?.writeText(link).then(() => toast("Link copied"))}
        >
          <Copy className="h-3.5 w-3.5" aria-hidden /> Copy
        </Button>
      </div>
    </Alert>
  );
}

function InviteForm({ roles, onSent }: { roles: RoleOption[]; onSent: (devLink?: string) => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState(roles[0]?.role ?? "");
  const [specialties, setSpecialties] = useState<string[]>([]);
  const [touched, setTouched] = useState(false);
  const emailProblem = /^\S+@\S+\.\S+$/.test(email) ? null : "Enter a valid email address.";
  const send = useMutation({
    // Specialties only for an HCP invitation; none chosen means "Specialty not configured".
    mutationFn: () => post("/invitations", role === "hcp" ? { email, role, specialties } : { email, role }),
    onSuccess: (data: Json) => {
      const sent = data.invitation.delivery === "email";
      toast(sent ? `Invitation emailed to ${data.invitation.email}` : "Invitation created");
      setEmail("");
      setSpecialties([]);
      setTouched(false);
      onSent(data.dev_link);
      void client.invalidateQueries({ queryKey: ["invitations"] });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (emailProblem || !role) return;
    send.mutate();
  }

  const selected = roles.find((r) => r.role === role);
  return (
    <form onSubmit={submit} noValidate className="space-y-5">
      <fieldset>
        <legend className="mb-2 text-sm font-medium text-ink">Role</legend>
        <div role="radiogroup" aria-label="Role to invite" className="grid gap-2 sm:grid-cols-2">
          {roles.map((r) => {
            const on = r.role === role;
            return (
              <button
                key={r.role}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => setRole(r.role)}
                className={
                  "min-h-12 rounded-lg border px-3 py-2.5 text-left text-sm font-semibold transition-[border-color,background-color] " +
                  (on ? "border-primary bg-primary-soft/60 text-ink" : "border-line text-ink hover:border-line-strong hover:bg-subtle/50")
                }
              >
                {r.label}
              </button>
            );
          })}
        </div>
        {selected && <p className="mt-2 text-[13px] leading-5 text-ink-subtle">{selected.description}</p>}
      </fieldset>
      {role === "hcp" && (
        <SpecialtyPicker
          value={specialties}
          onChange={setSpecialties}
          hint="Choose every specialty the HCP is qualified in. Leave empty if unknown: it shows as Specialty not configured. The HCP cannot change these; they can ask you to."
        />
      )}
      <TextField
        label="Recipient email"
        type="email"
        inputMode="email"
        autoComplete="off"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        onBlur={() => setTouched(true)}
        error={touched && emailProblem}
        hint="The invitation, and later the verification code, go only to this address. The role cannot be changed by the recipient."
        required
      />
      <ErrorNote error={send.error} />
      <Button type="submit" variant="primary" busy={send.isPending} className="w-full sm:w-auto">
        <Send className="h-4 w-4" aria-hidden /> {send.isPending ? "Sending" : "Send invitation"}
      </Button>
    </form>
  );
}

export default function Invitations() {
  const { can } = useAuth();
  const toast = useToast();
  const client = useQueryClient();
  const seesAll = can(P.INVITATION_READ_ALL);
  const [status, setStatus] = useState("all");
  const [devLink, setDevLink] = useState<string | null>(null);
  const options = useQuery({ queryKey: ["invitations", "options"], queryFn: () => api<RoleOption[]>("/invitations/options") });
  const list = useQuery({
    queryKey: ["invitations", "list", status],
    queryFn: () => api(`/invitations${query({ status: status === "all" ? undefined : status, limit: 200 })}`),
    placeholderData: (previous) => previous,
  });
  const act = useMutation({
    mutationFn: (v: { id: number; action: "reissue" | "revoke" }) => post(`/invitations/${v.id}/${v.action}`),
    onSuccess: (data: Json, v) => {
      toast(v.action === "revoke" ? "Invitation revoked. Its link no longer works." : "New invitation sent. The earlier link no longer works.");
      if (data.dev_link) setDevLink(data.dev_link);
      void client.invalidateQueries({ queryKey: ["invitations"] });
    },
  });

  const counts: Record<string, number> = list.data?.counts ?? {};
  const items: Json[] = list.data?.items ?? [];
  const shown = (n: number | undefined) => (list.data ? num(n ?? 0) : "—");

  const columns: Column<Json>[] = [
    {
      key: "email",
      header: "Invited",
      primary: true,
      className: "min-w-56",
      cell: (i) => (
        <div className="min-w-0">
          {i.accepted_user ? (
            <PersonName
              name={i.accepted_user.name}
              verified={i.accepted_user.professionally_verified}
              source={i.accepted_user.verification_source}
              className="max-w-full font-semibold text-ink"
            />
          ) : (
            <div className="truncate font-semibold text-ink">{i.email}</div>
          )}
          <div className="truncate text-[13px] text-ink-subtle">{i.accepted_user ? i.email : i.role_label}</div>
        </div>
      ),
    },
    {
      key: "role",
      header: "Role",
      cell: (i) => (
        <div className="min-w-0">
          <span className="text-ink">{i.role_label}</span>
          {i.role === "hcp" && <div className="text-[13px] text-ink-subtle">{specialtyText(i.specialties)}</div>}
        </div>
      ),
    },
    ...(seesAll
      ? [
          {
            key: "by",
            header: "Invited by",
            hideOnMobile: true,
            cell: (i: Json) => (
              <span className="inline-flex min-w-0 flex-col">
                <PersonName
                  name={i.invited_by?.name ?? "—"}
                  verified={i.invited_by?.professionally_verified}
                  source={i.invited_by?.verification_source}
                  className="text-ink"
                />
                <span className="text-[13px] text-ink-subtle">{i.inviter_role_label}</span>
              </span>
            ),
          } as Column<Json>,
        ]
      : []),
    { key: "status", header: "Status", hideOnMobile: true, cell: (i) => <InvitationStatus status={i.status} /> },
    { key: "delivery", header: "Email", cell: (i) => <Delivery value={i.delivery} /> },
    {
      key: "dates",
      header: "Sent / expires",
      cell: (i) => (
        <span className="tabular text-[13px] text-ink-muted">
          {fmtDateTime(i.created_at)}
          <br />
          {i.status === "accepted" ? `Accepted ${fmtDateTime(i.accepted_at)}` : `Expires ${fmtDateTime(i.expires_at)}`}
        </span>
      ),
    },
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      cell: (i) =>
        i.can_manage ? (
          <div className="flex flex-wrap gap-1.5">
            <Button
              size="sm"
              variant="ghost"
              busy={act.isPending && act.variables?.id === i.id && act.variables.action === "reissue"}
              onClick={(e) => {
                e.stopPropagation();
                act.mutate({ id: i.id, action: "reissue" });
              }}
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden /> Resend
            </Button>
            {i.status === "pending" && (
              <Button
                size="sm"
                variant="quiet-danger"
                busy={act.isPending && act.variables?.id === i.id && act.variables.action === "revoke"}
                onClick={(e) => {
                  e.stopPropagation();
                  act.mutate({ id: i.id, action: "revoke" });
                }}
              >
                <Ban className="h-3.5 w-3.5" aria-hidden /> Revoke
              </Button>
            )}
          </div>
        ) : null,
    },
  ];

  return (
    <>
      <PageHeader
        title={seesAll ? "Invitations" : "Team invitations"}
        subtitle={
          seesAll
            ? "Professional accounts are created only by invitation. Invite healthcare professionals, representatives, care managers and MLR reviewers, and see who invited whom."
            : "Invite the medical representative and care manager you work with. They join with the role you choose and the Verified mark."
        }
      />
      <KpiGrid>
        <Stat label="Pending" value={shown(counts.pending)} tone={counts.pending ? "warn" : undefined} icon={<Clock3 className="h-4 w-4" aria-hidden />} hint="waiting for the recipient" />
        <Stat label="Accepted" value={shown(counts.accepted)} tone="ok" icon={<CheckCircle2 className="h-4 w-4" aria-hidden />} hint="accounts created" />
        <Stat label="Expired" value={shown(counts.expired)} icon={<Clock3 className="h-4 w-4" aria-hidden />} hint="can be sent again" />
        <Stat label="Revoked" value={shown(counts.revoked)} icon={<Ban className="h-4 w-4" aria-hidden />} hint="links no longer work" />
      </KpiGrid>

      {devLink && <DevLink link={devLink} onClose={() => setDevLink(null)} />}
      <ErrorNote error={act.error} className="mb-4" />

      <div className="grid gap-6 xl:grid-cols-12">
        <Card title="Send an invitation" description="Links work once and expire after 72 hours." className="xl:col-span-4">
          {options.isLoading ? (
            <LoadingRows rows={3} label="Loading roles" />
          ) : options.error ? (
            <ErrorState error={options.error} retry={() => void options.refetch()} title="Roles could not be loaded" />
          ) : options.data?.length ? (
            <InviteForm roles={options.data} onSent={(link) => setDevLink(link ?? null)} />
          ) : (
            <EmptyState compact icon={<UserPlus className="h-5 w-5" />} title="No roles to invite">
              Your account cannot invite anyone.
            </EmptyState>
          )}
        </Card>

        <Card
          flush
          className="xl:col-span-8"
          title={seesAll ? "All invitations" : "Invitations you sent"}
        >
          {/* In the body rather than the card header, so it can scroll on narrow screens. */}
          <div className="min-w-0 border-b border-line px-4 py-3 sm:px-5">
            <Segmented
              label="Filter by status"
              value={status}
              onChange={setStatus}
              options={[
                { value: "all", label: "All" },
                { value: "pending", label: "Pending" },
                { value: "accepted", label: "Accepted" },
                { value: "expired", label: "Expired" },
                { value: "revoked", label: "Revoked" },
              ]}
            />
          </div>
          {list.isLoading ? (
            <div className="p-5">
              <LoadingRows rows={5} label="Loading invitations" />
            </div>
          ) : list.error ? (
            <ErrorState error={list.error} retry={() => void list.refetch()} title="Invitations could not be loaded" />
          ) : !items.length ? (
            <EmptyState icon={<MailPlus className="h-5 w-5" />} title={status === "all" ? "No invitations yet" : "None with this status"}>
              {status === "all" ? "Invitations you send appear here with their status." : "Choose another status to see more."}
            </EmptyState>
          ) : (
            <DataTable
              caption="Invitations"
              tableFrom="3xl"
              columns={columns}
              rows={items}
              rowKey={(i) => i.id}
              mobileAside={(i) => <InvitationStatus status={i.status} />}
            />
          )}
        </Card>
      </div>
    </>
  );
}
