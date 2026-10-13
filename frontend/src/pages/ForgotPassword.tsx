// Forgot password: email -> code -> new password, in one dialog on the sign-in page.
//
// Every decision is the server's (POST /api/auth/password-reset/*): whether the account is
// eligible (never an administrator), whether the code is right, whether this browser holds
// the reset authorization. The dialog only moves forward on a successful answer, and the
// transaction and authorization live in HttpOnly cookies it cannot read or forge. The
// first answer is the same for every address, so this dialog never says whether an
// account exists.
import { KeyRound, MailCheck, ShieldCheck, TerminalSquare, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { createPortal } from "react-dom";
import { ApiError, post } from "../api";
import type { Json } from "../api";
import { Alert, Button, ErrorNote, PasswordField, TextField, cx, keepFocusInside } from "../ui";
import { CodeInput } from "./VerifyOtp";

type Step = "email" | "code" | "password" | "done";

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PASSWORD_MIN = 10;

export const passwordRules = (password: string) => [
  { label: `At least ${PASSWORD_MIN} characters`, met: password.length >= PASSWORD_MIN },
  { label: "At least one letter", met: /[A-Za-z]/.test(password) },
  { label: "At least one number", met: /\d/.test(password) },
];

/** A centred modal: focus moves in, stays in (Tab), Escape and the backdrop close it, and
 *  focus returns to whatever opened it. */
function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    if (!panel.current?.contains(document.activeElement)) panel.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") return closeRef.current();
      if (panel.current) keepFocusInside(e, panel.current);
    };
    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previous?.focus?.();
    };
  }, []);
  return createPortal(
    <div style={{ zIndex: "var(--z-drawer)", position: "relative" }}>
      <div className="animate-fade fixed inset-0 bg-black/45" aria-hidden onClick={() => closeRef.current()} />
      <div className="pointer-events-none fixed inset-0 grid place-items-center overflow-y-auto p-4 sm:p-6">
        <div
          ref={panel}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          tabIndex={-1}
          className="pointer-events-auto w-full max-w-md rounded-2xl border border-line bg-surface p-6 shadow-overlay outline-none sm:p-7"
        >
          <div className="mb-5 flex items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary-soft text-primary-ink">
                <KeyRound className="h-5 w-5" aria-hidden />
              </span>
              <h2 id={titleId} className="text-lg font-semibold text-ink">
                {title}
              </h2>
            </div>
            <button
              type="button"
              onClick={() => closeRef.current()}
              aria-label="Close"
              className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-ink-subtle hover:bg-subtle hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function Progress({ step }: { step: Step }) {
  const steps: Array<[Step, string]> = [
    ["email", "Email"],
    ["code", "Code"],
    ["password", "New password"],
  ];
  const at = step === "done" ? 3 : steps.findIndex(([s]) => s === step);
  return (
    <ol className="mb-6 grid grid-cols-3 gap-2" aria-label="Progress">
      {steps.map(([key, label], i) => (
        <li key={key} aria-current={i === at ? "step" : undefined} className="min-w-0">
          <div className={cx("h-1 rounded-full", i <= at ? "bg-primary" : "bg-line")} />
          <div className={cx("mt-1.5 truncate text-xs", i === at ? "font-semibold text-ink" : "text-ink-subtle")}>{label}</div>
        </li>
      ))}
    </ol>
  );
}

export function ForgotPasswordDialog({
  initialEmail = "",
  onClose,
  onDone,
}: {
  initialEmail?: string;
  onClose: () => void;
  /** After a successful reset: back to the sign-in form with this email filled in. */
  onDone?: (email: string) => void;
}) {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState(initialEmail);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [failures, setFailures] = useState(0);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [started, setStarted] = useState<Json | null>(null);
  const [resendAt, setResendAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (step !== "code") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [step]);
  const resendIn = Math.max(0, Math.ceil((resendAt - now) / 1000));

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  function begin(answer: Json) {
    setStarted(answer);
    setResendAt(Date.now() + (answer.resend_in ?? 30) * 1000);
    setNow(Date.now());
  }

  const requestCode = (event: FormEvent) => {
    event.preventDefault();
    const value = email.trim();
    if (!EMAIL_PATTERN.test(value)) {
      setEmailError("Enter a valid email address.");
      return;
    }
    setEmailError(null);
    void run(async () => {
      begin(await post("/auth/password-reset/request", { email: value }));
      setCode("");
      setStep("code");
    });
  };

  const resend = () =>
    run(async () => {
      begin(await post("/auth/password-reset/resend"));
      setCode("");
      setNotice("If this account is eligible, a new code is on its way. Earlier codes no longer work.");
    });

  const verify = (event?: FormEvent) => {
    event?.preventDefault();
    if (code.length !== 6) return;
    void run(async () => {
      try {
        await post("/auth/password-reset/verify", { code });
        setStep("password");
      } catch (e) {
        setFailures((n) => n + 1);
        setCode("");
        // A spent or ended transaction cannot continue: go back to the start.
        if (e instanceof ApiError && (e.code === "reset_expired" || e.code === "reset_unavailable")) setStep("email");
        throw e;
      }
    });
  };

  const save = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      try {
        await post("/auth/password-reset/complete", { password, confirm_password: confirm });
        setPassword("");
        setConfirm("");
        setStep("done");
      } catch (e) {
        if (e instanceof ApiError && ["reset_expired", "reset_unauthorized", "reset_unavailable"].includes(e.code ?? "")) {
          setStep("email");
        }
        throw e;
      }
    });
  };

  const rules = passwordRules(password);
  const mismatch = confirm.length > 0 && confirm !== password;
  const development = started?.delivery === "development";

  return (
    <Modal title="Reset your password" onClose={onClose}>
      {step !== "done" && <Progress step={step} />}

      {step === "email" && (
        <form onSubmit={requestCode} noValidate className="space-y-5">
          <p className="text-sm text-ink-muted">
            Enter the email you sign in with. If the account can be recovered here, we'll email it a 6-digit code.
          </p>
          <TextField
            label="Email"
            type="email"
            autoComplete="email"
            inputMode="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            error={emailError ?? undefined}
            required
            autoFocus
          />
          <ErrorNote error={error} />
          <Button type="submit" variant="primary" size="lg" busy={busy} className="w-full">
            {busy ? "Sending" : "Continue"}
          </Button>
        </form>
      )}

      {step === "code" && (
        <form onSubmit={verify} className="space-y-5">
          {development ? (
            <Alert tone="warn" icon={<TerminalSquare className="h-5 w-5" aria-hidden />} title="Development mode: no email was sent">
              No mail server is configured here, so a code is shown instead. It only works if the account is eligible.
              {started?.dev_otp && (
                <div className="tabular mt-3 select-all rounded-lg border border-warn-line bg-surface px-3 py-2 text-center text-2xl font-semibold tracking-[0.4em] text-ink">
                  {started.dev_otp}
                </div>
              )}
            </Alert>
          ) : (
            <Alert tone="info" icon={<MailCheck className="h-5 w-5" aria-hidden />}>
              <span className="text-info">
                If <span className="font-semibold">{email.trim()}</span> can be recovered here, a code is on its way. It
                expires in {Math.round((started?.expires_in ?? 600) / 60)} minutes. Check your spam folder too.
              </span>
            </Alert>
          )}
          <CodeInput value={code} onChange={setCode} invalid={Boolean(error)} disabled={busy} failures={failures} verified={false} />
          {notice && <p className="text-[13px] text-ink-muted" role="status">{notice}</p>}
          <ErrorNote error={error} />
          <Button type="submit" variant="primary" size="lg" busy={busy} disabled={code.length !== 6} className="w-full">
            {busy ? "Checking" : "Verify"}
          </Button>
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <button
              type="button"
              className="font-semibold text-primary-ink underline-offset-4 hover:underline disabled:cursor-not-allowed disabled:text-ink-subtle disabled:no-underline"
              disabled={busy || resendIn > 0}
              onClick={() => void resend()}
            >
              {resendIn > 0 ? `Resend code in ${resendIn}s` : "Resend code"}
            </button>
            <button
              type="button"
              className="text-ink-muted underline-offset-4 hover:text-ink hover:underline"
              onClick={() => {
                setError(null);
                setNotice(null);
                setStep("email");
              }}
            >
              Use a different email
            </button>
          </div>
        </form>
      )}

      {step === "password" && (
        <form onSubmit={save} className="space-y-5">
          <p className="text-sm text-ink-muted">Code confirmed. Choose a new password for {email.trim()}.</p>
          <PasswordField
            label="New password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoFocus
          />
          <ul className="space-y-1 text-[13px]" aria-label="Password requirements">
            {rules.map((r) => (
              <li key={r.label} className={cx("flex items-center gap-2", r.met ? "text-ok" : "text-ink-subtle")}>
                <span aria-hidden>{r.met ? "✓" : "•"}</span>
                {r.label}
                <span className="sr-only">{r.met ? "(met)" : "(not met yet)"}</span>
              </li>
            ))}
          </ul>
          <PasswordField
            label="Confirm new password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            error={mismatch ? "The two passwords do not match." : undefined}
            required
          />
          <ErrorNote error={error} />
          <Button
            type="submit"
            variant="primary"
            size="lg"
            busy={busy}
            disabled={!rules.every((r) => r.met) || !confirm || mismatch}
            className="w-full"
          >
            {busy ? "Saving" : "Save password"}
          </Button>
        </form>
      )}

      {step === "done" && (
        <div className="space-y-5">
          <Alert tone="ok" icon={<ShieldCheck className="h-5 w-5" aria-hidden />} title="Password changed">
            <span className="text-ok">
              Sign in with your new password. For your security, every device that was signed in has been signed out.
            </span>
          </Alert>
          <Button
            variant="primary"
            size="lg"
            className="w-full"
            autoFocus
            onClick={() => {
              onDone?.(email.trim());
              onClose();
            }}
          >
            Back to sign in
          </Button>
        </div>
      )}
    </Modal>
  );
}
