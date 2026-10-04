import { MailOpen, UserRound } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { Button, ErrorNote } from "../ui";
import { AccountFields, accountProblems, fieldError, useAccountForm } from "./AccountFields";
import AuthLayout from "./AuthLayout";

/** Patient self-registration. There is no role choice: the server creates a patient account,
 *  and professional roles exist only through an invitation. */
export default function Signup() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const form = useAccountForm();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const invalid = Object.values(accountProblems(form.values, false)).some(Boolean);

  async function submit(event: FormEvent) {
    event.preventDefault();
    form.setSubmitted(true);
    if (invalid) return;
    setBusy(true);
    setError(null);
    try {
      await signup(form.values);
      navigate("/signup/verify");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      wide
      title="Create your patient account"
      subtitle="See your medications, messages from your care team and your contact preferences in one place."
      footer={
        <>
          Already registered?{" "}
          <Link to="/login" className="font-semibold text-primary-ink underline-offset-4 hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-7" noValidate>
        <div className="flex items-start gap-3 rounded-xl border border-line bg-subtle/60 p-4">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[10px] bg-primary-soft text-primary-ink" aria-hidden>
            <UserRound className="h-5 w-5" />
          </span>
          <div className="min-w-0 text-[13px] leading-5 text-ink-muted">
            <div className="text-[15px] font-semibold text-ink">Patient account</div>
            Your medications, messages and contact preferences.
          </div>
        </div>

        <AccountFields form={form} serverError={error} />

        {!fieldError(error) && <ErrorNote error={error} />}
        <Button type="submit" variant="primary" size="lg" busy={busy} className="w-full sm:w-auto">
          {busy ? "Creating account" : "Create account"}
        </Button>

        <p className="flex items-start gap-2 border-t border-line pt-5 text-[13px] leading-5 text-ink-subtle">
          <MailOpen className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>
            Healthcare professionals, representatives, care managers and compliance reviewers join by
            invitation. Use the link in your invitation email.
          </span>
        </p>
      </form>
    </AuthLayout>
  );
}
