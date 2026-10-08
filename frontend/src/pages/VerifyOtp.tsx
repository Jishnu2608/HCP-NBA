import { MailCheck, MailX, TerminalSquare, TimerReset } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { DUR, gsap, reducedMotion, useGSAP } from "../motion";
import type { ClipboardEvent, FormEvent, KeyboardEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { pendingSignup } from "../session";
import type { Challenge } from "../session";
import { useToast } from "../toast";
import { Alert, Button, EmailText, ErrorNote, cx } from "../ui";
import AuthLayout from "./AuthLayout";

const LENGTH = 6;
const seconds = (until: number, now: number) => Math.max(0, Math.ceil((until - now) / 1000));
const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/**
 * Six single-digit boxes that behave like one field: typing advances, Backspace goes back,
 * pasting or SMS/email autofill of the whole code fills every box.
 */
function CodeInput({
  value,
  onChange,
  invalid,
  disabled,
  failures,
  verified,
}: {
  value: string;
  onChange: (code: string) => void;
  invalid: boolean;
  disabled: boolean;
  /** Increments on each rejected code: the row gives one small shake. */
  failures: number;
  /** The code was accepted: the boxes turn green left to right. */
  verified: boolean;
}) {
  const boxes = useRef<Array<HTMLInputElement | null>>([]);
  const group = useRef<HTMLDivElement>(null);
  const digits = Array.from({ length: LENGTH }, (_, i) => value[i] ?? "");
  const before = useRef(value);
  // A box that just received a digit settles with a very small pop.
  useGSAP(
    () => {
      const was = before.current;
      before.current = value;
      if (reducedMotion() || value.length <= was.length) return;
      const filled = boxes.current.slice(was.length, value.length).filter(Boolean);
      if (filled.length) gsap.fromTo(filled, { scale: 1.08 }, { scale: 1, duration: DUR.micro, stagger: 0.02, clearProps: "transform" });
    },
    { dependencies: [value] },
  );
  // A rejected code: one short, small shake of the row only (none under reduced motion).
  useGSAP(
    () => {
      if (!failures || !group.current || reducedMotion()) return;
      gsap.fromTo(group.current, { x: 0 }, { keyframes: { x: [-4, 4, -3, 3, 0] }, duration: 0.25, ease: "none", clearProps: "transform" });
    },
    { dependencies: [failures] },
  );
  // Accepted: a quick left-to-right confirmation across the boxes.
  useGSAP(
    () => {
      if (!verified || reducedMotion()) return;
      gsap.fromTo(boxes.current.filter(Boolean), { scale: 1 }, { keyframes: { scale: [1, 1.06, 1] }, duration: 0.24, stagger: 0.03 });
    },
    { dependencies: [verified] },
  );
  const focus = (i: number) => boxes.current[Math.max(0, Math.min(LENGTH - 1, i))]?.focus();

  function write(index: number, raw: string) {
    let typed = raw.replace(/\D/g, "");
    if (!typed) return;
    // Typing into a filled box (caret after the old digit) replaces it with the new one.
    if (digits[index] && typed.length === 2) typed = typed.startsWith(digits[index]) ? typed[1] : typed[0];
    const chars = [...digits];
    [...typed].forEach((c, k) => {
      if (index + k < LENGTH) chars[index + k] = c;
    });
    const next = chars.join("");
    onChange(next);
    focus(Math.min(index + typed.length, LENGTH - 1));
  }

  function onKey(index: number, e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Backspace") {
      e.preventDefault();
      if (digits[index]) onChange(value.slice(0, index) + value.slice(index + 1));
      else if (index > 0) {
        onChange(value.slice(0, index - 1) + value.slice(index));
        focus(index - 1);
      }
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      focus(index - 1);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      focus(index + 1);
    }
  }

  function onPaste(e: ClipboardEvent<HTMLInputElement>) {
    e.preventDefault();
    write(0, e.clipboardData.getData("text"));
  }

  return (
    <div ref={group} role="group" aria-label="Verification code" className="flex justify-between gap-2 sm:gap-3">
      {digits.map((d, i) => (
        <input
          key={i}
          ref={(el) => {
            boxes.current[i] = el;
          }}
          value={d}
          onChange={(e) => write(i, e.target.value)}
          onKeyDown={(e) => onKey(i, e)}
          onPaste={onPaste}
          onFocus={(e) => e.target.select()}
          inputMode="numeric"
          pattern="[0-9]*"
          autoComplete={i === 0 ? "one-time-code" : "off"}
          autoFocus={i === 0}
          disabled={disabled}
          aria-label={`Digit ${i + 1} of ${LENGTH}`}
          aria-invalid={invalid || undefined}
          maxLength={LENGTH}
          className={cx(
            "tabular h-14 w-full min-w-0 max-w-14 rounded-lg border bg-surface text-center text-2xl font-semibold text-ink shadow-card",
            "transition-[border-color,box-shadow] focus:border-primary focus:outline-none focus:ring-[3px] focus:ring-primary/20",
            verified ? "border-ok bg-ok-soft text-ok" : invalid ? "border-bad" : d ? "border-primary-line" : "border-line-strong",
          )}
        />
      ))}
    </div>
  );
}

export default function VerifyOtp() {
  const { verify, resend } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const [challenge, setChallenge] = useState<Challenge | null>(() => pendingSignup.get());
  const [code, setCode] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<"verify" | "resend" | null>(null);
  const [sent, setSent] = useState(false);
  const [failures, setFailures] = useState(0);
  const [verified, setVerified] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  if (!challenge) return <Navigate to="/signup" replace />;

  const expiresIn = seconds(challenge.expiresAt, now);
  const resendIn = seconds(challenge.resendAt, now);
  const development = challenge.delivery === "development";
  // The email could not be sent: there is no code to enter until a resend succeeds.
  const failed = challenge.delivery === "failed";

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (code.length !== LENGTH) return;
    setBusy("verify");
    setError(null);
    try {
      const user = await verify(code);
      // A brief confirmation on the boxes before the workspace opens (at most 250 ms).
      setVerified(true);
      await new Promise((resolve) => window.setTimeout(resolve, reducedMotion() ? 0 : 250));
      toast(`Email verified. Welcome, ${user.name.split(" ")[0]}.`);
      navigate(user.home, { replace: true });
    } catch (e) {
      setError(e);
      setFailures((n) => n + 1);
      setCode("");
    } finally {
      setBusy(null);
    }
  }

  async function again() {
    setBusy("resend");
    setError(null);
    setSent(false);
    try {
      setChallenge(await resend());
      setCode("");
      setSent(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <AuthLayout
      step={2}
      title="Check your email"
      subtitle={
        <>
          Enter the 6-digit code for{" "}
          <EmailText email={challenge.email} className="font-semibold text-ink" /> to finish setting up your
          account.
        </>
      }
      footer={
        <Link
          to="/login"
          className="font-semibold text-primary-ink underline-offset-4 hover:underline"
          onClick={() => pendingSignup.clear()}
        >
          Back to sign in
        </Link>
      }
    >
      {busy === "resend" ? (
        <Alert tone="info" icon={<MailCheck className="h-5 w-5" aria-hidden />} className="mb-6" title="Sending a new code">
          Sending a new code to <EmailText email={challenge.email} className="font-semibold" />. This can take a few seconds.
        </Alert>
      ) : failed ? (
        <Alert tone="bad" icon={<MailX className="h-5 w-5" aria-hidden />} title="We couldn't send the verification email" className="mb-6">
          <span className="text-bad">
            The code could not be delivered to <EmailText email={challenge.email} className="font-semibold" />. Your
            account is saved but not active yet. Check the address, then send a new code below.
          </span>
        </Alert>
      ) : development ? (
        <Alert
          tone="warn"
          icon={<TerminalSquare className="h-5 w-5" aria-hidden />}
          title="Development mode: no email was sent"
          className="mb-6"
        >
          No mail server is configured here, so the code is shown below instead. In production it goes only to the
          inbox.
          {challenge.dev_otp ? (
            <div className="tabular mt-3 select-all rounded-lg border border-warn-line bg-surface px-3 py-2 text-center text-2xl font-semibold tracking-[0.4em] text-ink">
              {challenge.dev_otp}
            </div>
          ) : (
            <div className="mt-2 font-medium text-warn">Request a new code below to display it.</div>
          )}
        </Alert>
      ) : (
        <Alert tone="ok" icon={<MailCheck className="h-5 w-5" aria-hidden />} className="mb-6">
          <span className="text-ok">We emailed a code to {challenge.email}. Check your inbox and spam folder.</span>
        </Alert>
      )}

      <form onSubmit={submit} className="space-y-5">
        <CodeInput
          value={code}
          onChange={(next) => {
            setCode(next);
            setError(null);
          }}
          invalid={Boolean(error)}
          disabled={busy === "verify" || failed || verified}
          failures={failures}
          verified={verified}
        />
        <div className="flex flex-wrap items-center justify-between gap-2 text-[13px] text-ink-subtle" aria-live="polite">
          <span className={cx("tabular", expiresIn === 0 && !failed && "font-medium text-bad")}>
            {busy === "resend"
              ? "Sending…"
              : failed
              ? "No code has been sent yet."
              : expiresIn > 0
                ? `Code expires in ${clock(expiresIn)}`
                : "This code has expired. Request a new one."}
          </span>
          <span>Single use · 5 attempts</span>
        </div>
        <ErrorNote error={error} />
        {sent && !error && (
          <p role="status" className="text-[13px] font-medium text-ok">
            A new code is on its way. Earlier codes no longer work.
          </p>
        )}
        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full"
          busy={busy === "verify"}
          disabled={code.length !== LENGTH || failed}
        >
          {verified ? "Verified" : busy === "verify" ? "Verifying" : "Verify and continue"}
        </Button>
      </form>

      <div className="mt-4 flex justify-center">
        <Button
          variant={failed ? "primary" : "ghost"}
          onClick={() => void again()}
          busy={busy === "resend"}
          disabled={resendIn > 0}
        >
          <TimerReset className="h-4 w-4" aria-hidden />
          {resendIn > 0 ? <span className="tabular">Send a new code in {resendIn}s</span> : "Send a new code"}
        </Button>
      </div>
    </AuthLayout>
  );
}
