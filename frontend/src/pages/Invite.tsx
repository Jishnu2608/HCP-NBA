import { useQuery } from "@tanstack/react-query";
import {
  BadgeCheck,
  Clock3,
  Link2Off,
  LogIn,
  LogOut,
  MailWarning,
  RefreshCw,
  ShieldX,
  UserRoundCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, post } from "../api";
import { useAuth } from "../auth";
import { Button, ErrorNote, Skeleton, fmtDateTime } from "../ui";
import { AccountFields, accountProblems, fieldError, useAccountForm } from "./AccountFields";
import AuthLayout from "./AuthLayout";

interface InvitationInfo {
  email: string;
  role: string;
  role_label: string;
  inviter_name: string;
  inviter_role_label: string;
  expires_at: string;
  account_exists: boolean;
}

const signInLink = (
  <Link to="/login" className="font-semibold text-primary-ink underline-offset-4 hover:underline">
    Sign in
  </Link>
);

/** One clear panel per way an invitation link can fail, each with a way forward. The raw
 *  token is never shown. */
function Problem({
  icon,
  title,
  children,
  actions,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-line bg-surface p-5 shadow-card">
      <div className="flex items-start gap-3.5">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[10px] bg-subtle text-ink-muted" aria-hidden>
          {icon}
        </span>
        <div className="min-w-0">
          <h2 className="text-[17px] font-semibold text-ink">{title}</h2>
          <div className="mt-1.5 text-sm leading-6 text-ink-muted">{children}</div>
        </div>
      </div>
      {actions && <div className="mt-5 flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

function LookupFailed({ error, retry }: { error: unknown; retry: () => void }) {
  const code = error instanceof ApiError ? error.code : null;
  const inviter = error instanceof ApiError ? (error.detail?.inviter_name as string | undefined) : undefined;
  const ask = inviter ? `Ask ${inviter}` : "Ask the person who invited you";
  if (code === "invitation_expired")
    return (
      <Problem icon={<Clock3 className="h-5 w-5" />} title="This invitation has expired">
        Invitations work for a limited time. {ask} to send you a new one; it will arrive as a new email.
      </Problem>
    );
  if (code === "invitation_used")
    return (
      <Problem
        icon={<UserRoundCheck className="h-5 w-5" />}
        title="This invitation has already been used"
        actions={
          <Link to="/login">
            <Button variant="primary">
              <LogIn className="h-4 w-4" aria-hidden /> Sign in
            </Button>
          </Link>
        }
      >
        An account was created with it. Sign in with that account's email and password.
      </Problem>
    );
  if (code === "invitation_revoked")
    return (
      <Problem icon={<ShieldX className="h-5 w-5" />} title="This invitation is no longer valid">
        It was withdrawn or replaced by a newer invitation. Check your email for a more recent one, or {ask.toLowerCase()}{" "}
        to send it again.
      </Problem>
    );
  if (code === "rate_limited")
    return (
      <Problem icon={<Clock3 className="h-5 w-5" />} title="Too many attempts">
        Please wait a few minutes, then open the link from your email again.
      </Problem>
    );
  if (code === "invitation_invalid")
    return (
      <Problem icon={<Link2Off className="h-5 w-5" />} title="This invitation link isn't valid">
        <p>Email programs sometimes break long links across lines. Copy the whole link from the invitation email and
          paste it into your browser's address bar.</p>
        <p className="mt-2">If it still doesn't work, ask the person who invited you to send a new invitation.</p>
      </Problem>
    );
  return (
    <Problem
      icon={<MailWarning className="h-5 w-5" />}
      title="We couldn't open this invitation"
      actions={
        <Button onClick={retry}>
          <RefreshCw className="h-4 w-4" aria-hidden /> Try again
        </Button>
      }
    >
      Check your connection and try again. You can also copy the link from the email into another browser or device.
    </Problem>
  );
}

export default function Invite() {
  const { token = "" } = useParams();
  const { user, acceptInvitation, logout } = useAuth();
  const navigate = useNavigate();
  const lookup = useQuery({
    queryKey: ["invitation", token],
    queryFn: () => post<InvitationInfo>("/invitations/lookup", { token }),
    retry: false,
    staleTime: Infinity,
  });
  const info = lookup.data;
  const form = useAccountForm();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const { setValues } = form;
  useEffect(() => {
    // Shown read-only for reference; it is not sent, the server uses the invited address.
    if (info) setValues((v) => ({ ...v, email: info.email }));
  }, [info, setValues]);
  const values = form.values;
  const invalid = Object.values(accountProblems(values, true)).some(Boolean);

  async function submit(event: FormEvent) {
    event.preventDefault();
    form.setSubmitted(true);
    if (invalid) return;
    setBusy(true);
    setError(null);
    try {
      // Only these fields are sent. The role and email come from the invitation on the server.
      await acceptInvitation({
        token,
        name: values.name,
        date_of_birth: values.date_of_birth,
        password: values.password,
        confirm_password: values.confirm_password,
      });
      navigate("/signup/verify");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  let body: ReactNode;
  if (lookup.isLoading) {
    body = (
      <div className="space-y-4" role="status" aria-label="Checking your invitation">
        <Skeleton className="h-24 rounded-xl" />
        <Skeleton className="h-64 rounded-xl" />
      </div>
    );
  } else if (lookup.error) {
    body = <LookupFailed error={lookup.error} retry={() => void lookup.refetch()} />;
  } else if (info && user) {
    body = (
      <Problem
        icon={<LogOut className="h-5 w-5" />}
        title={`You're signed in as ${user.name}`}
        actions={
          <Button variant="primary" onClick={() => void logout()}>
            <LogOut className="h-4 w-4" aria-hidden /> Sign out to accept
          </Button>
        }
      >
        This invitation is for <span className="break-all font-semibold text-ink">{info.email}</span>. Sign out of{" "}
        <span className="break-all">{user.email}</span> first, then accept the invitation here.
      </Problem>
    );
  } else if (info?.account_exists) {
    body = (
      <Problem
        icon={<UserRoundCheck className="h-5 w-5" />}
        title="An account already exists for this email"
        actions={
          <Link to="/login">
            <Button variant="primary">
              <LogIn className="h-4 w-4" aria-hidden /> Sign in
            </Button>
          </Link>
        }
      >
        <span className="break-all font-semibold text-ink">{info.email}</span> already has an account. Sign in with it,
        or contact the person who invited you if you think this is wrong.
      </Problem>
    );
  } else if (info) {
    body = (
      <form onSubmit={submit} className="space-y-7" noValidate>
        <div className="rounded-xl border border-primary-line bg-primary-soft/50 p-4">
          <div className="flex items-start gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[10px] bg-primary text-on-primary" aria-hidden>
              <BadgeCheck className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <div className="text-xs font-medium text-ink-subtle">Role · set by your invitation</div>
              <div className="text-[17px] font-semibold text-ink">{info.role_label}</div>
              <div className="mt-1 text-[13px] leading-5 text-ink-muted">
                Invited by <span className="font-semibold text-ink">{info.inviter_name}</span> ({info.inviter_role_label}).
                This invitation can be used once and expires {fmtDateTime(`${info.expires_at}Z`)}.
              </div>
            </div>
          </div>
        </div>

        <AccountFields form={form} emailFixed serverError={error} />

        {!fieldError(error) && <ErrorNote error={error} />}
        <Button type="submit" variant="primary" size="lg" busy={busy} className="w-full sm:w-auto">
          {busy ? "Sending code" : "Accept and continue"}
        </Button>
        <p className="text-[13px] leading-5 text-ink-subtle">
          Next, we email a 6-digit code to {info.email}. Your account becomes active, with the Verified mark, once you
          enter it.
        </p>
      </form>
    );
  }

  return (
    <AuthLayout
      wide
      title="Accept your invitation"
      subtitle="Join Next Best Action with the role your organization assigned to you."
      footer={<>Already have an account? {signInLink}</>}
    >
      {body}
    </AuthLayout>
  );
}
