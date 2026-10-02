import { MailCheck, TerminalSquare } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { pendingSignup } from "../session";
import type { Challenge } from "../session";
import { Button, ErrorNote } from "../ui";
import AuthLayout from "./AuthLayout";

const seconds = (until: number, now: number) => Math.max(0, Math.ceil((until - now) / 1000));
const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

export default function VerifyOtp() {
  const { verify, resend } = useAuth();
  const navigate = useNavigate();
  const [challenge, setChallenge] = useState<Challenge | null>(() => pendingSignup.get());
  const [code, setCode] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<"verify" | "resend" | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  if (!challenge) return <Navigate to="/signup" replace />;

  const expiresIn = seconds(challenge.expiresAt, now);
  const resendIn = seconds(challenge.resendAt, now);
  const development = challenge.delivery === "development";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy("verify");
    setError(null);
    try {
      const user = await verify(code);
      navigate(user.home, { replace: true });
    } catch (e) {
      setError(e);
      setCode("");
    } finally {
      setBusy(null);
    }
  }

  async function again() {
    setBusy("resend");
    setError(null);
    try {
      setChallenge(await resend());
      setCode("");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <AuthLayout
      title="Verify your email"
      subtitle={
        <>
          Enter the 6-digit code for <span className="font-medium text-stone-800">{challenge.email}</span>{" "}
          to finish creating your account.
        </>
      }
    >
      {development ? (
        <div className="mb-5 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">
          <div className="flex items-center gap-2 font-semibold">
            <TerminalSquare className="h-4 w-4" /> Development mode: no email was sent
          </div>
          <p className="mt-1">
            No mail server is configured for this environment, so the code is shown here instead.
            In production it is delivered only to your inbox.
          </p>
          {challenge.dev_otp ? (
            <div className="tabular mt-3 select-all rounded-lg bg-white px-3 py-2 text-center text-2xl font-semibold tracking-[0.4em] text-stone-900 ring-1 ring-amber-200">
              {challenge.dev_otp}
            </div>
          ) : (
            <p className="mt-2 font-medium">Request a new code below to display it.</p>
          )}
        </div>
      ) : (
        <div className="mb-5 flex items-start gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">
          <MailCheck className="mt-0.5 h-4 w-4 shrink-0" />
          <span>We emailed a code to {challenge.email}. Check your inbox and spam folder.</span>
        </div>
      )}

      <form onSubmit={submit} className="space-y-4">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-stone-600">Verification code</span>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            placeholder="000000"
            className="tabular w-full rounded-lg border border-stone-300 px-3 py-3 text-center text-2xl tracking-[0.5em] outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          />
        </label>
        <div className="flex items-center justify-between text-xs text-stone-500">
          <span>
            {expiresIn > 0 ? `Code expires in ${clock(expiresIn)}` : "This code has expired. Request a new one."}
          </span>
          <span>Single use</span>
        </div>
        <ErrorNote error={error} />
        <Button
          type="submit"
          variant="primary"
          className="w-full"
          busy={busy === "verify"}
          disabled={code.length !== 6}
        >
          Verify and continue
        </Button>
      </form>

      <div className="mt-5 flex items-center justify-between text-sm">
        <Button variant="ghost" onClick={() => void again()} busy={busy === "resend"} disabled={resendIn > 0}>
          {resendIn > 0 ? `Send a new code in ${resendIn}s` : "Send a new code"}
        </Button>
        <Link to="/login" className="text-stone-500 hover:underline" onClick={() => pendingSignup.clear()}>
          Back to log in
        </Link>
      </div>
    </AuthLayout>
  );
}
