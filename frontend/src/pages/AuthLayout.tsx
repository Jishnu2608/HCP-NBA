import { Activity, Lock, ShieldCheck, Sparkles } from "lucide-react";
import { useRef } from "react";
import type { ReactNode } from "react";
import { gsap, reducedMotion, useEntrance, useGSAP } from "../motion";
import { Link } from "react-router-dom";
import { LegalLinks } from "../legal";
import { ThemeToggle } from "../theme";
import { cx } from "../ui";

/** The product mark: a pulse line in a rounded clinical-blue tile. */
export function BrandMark({ small = false }: { small?: boolean }) {
  return (
    <span
      aria-hidden
      className={cx(
        "grid shrink-0 place-items-center rounded-[10px] bg-primary text-on-primary ring-1 ring-inset ring-white/10",
        small ? "h-8 w-8" : "h-9 w-9",
      )}
    >
      <Activity className={small ? "h-4 w-4" : "h-[18px] w-[18px]"} strokeWidth={2.25} />
    </span>
  );
}

export function Brand({ light = false }: { light?: boolean }) {
  return (
    <Link to="/" className="inline-flex items-center gap-2.5 rounded-lg" aria-label="Next Best Action home">
      <BrandMark />
      <span className={cx("text-[17px] font-semibold tracking-[-0.01em]", light ? "text-white" : "text-ink")}>
        Next Best Action
      </span>
    </Link>
  );
}

const ASSURANCES = [
  { icon: Lock, text: "Your role comes from your account and is checked on the server for every request." },
  { icon: ShieldCheck, text: "Content approval, consent and contact limits are enforced before anything is sent." },
  { icon: Sparkles, text: "Every recommendation explains who, what, which channel and why now." },
];

const STEPS = ["Your details", "Verify email", "Account ready"];

/**
 * Where a new account is in setting up: details, then the emailed code, then ready. The
 * marker glides to the current step when the page changes (sign-up to verification).
 */
function SetupSteps({ step }: { step: 1 | 2 | 3 }) {
  const ref = useRef<HTMLOListElement>(null);
  useGSAP(
    () => {
      const marker = ref.current?.querySelector<HTMLElement>("[data-step-marker]");
      if (!marker || reducedMotion()) return;
      // Arriving on step 2 or 3 from the previous step: the marker travels from there.
      if (step > 1) gsap.from(marker, { xPercent: -100, duration: 0.45, ease: "power3.out" });
    },
    { scope: ref },
  );
  return (
    <ol ref={ref} aria-label="Account setup" className="mb-8 grid grid-cols-3 gap-2">
      {STEPS.map((label, i) => {
        const n = i + 1;
        const state = n < step ? "done" : n === step ? "current" : "todo";
        return (
          <li key={label} className="min-w-0">
            <div className="relative h-1 overflow-hidden rounded-full bg-sunken">
              {state === "done" && <span className="absolute inset-0 bg-ok-fill" aria-hidden />}
              {state === "current" && <span data-step-marker className="absolute inset-0 bg-primary" aria-hidden />}
            </div>
            <p className={cx("mt-1.5 truncate text-xs", state === "current" ? "font-semibold text-ink" : "text-ink-subtle")}>
              {label}
              <span className="sr-only">{state === "done" ? ": done" : state === "current" ? ": current step" : ""}</span>
            </p>
          </li>
        );
      })}
    </ol>
  );
}

/** Shared frame for the sign-in, sign-up and verification pages. */
export default function AuthLayout({
  title,
  subtitle,
  children,
  wide = false,
  footer,
  step,
}: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  wide?: boolean;
  footer?: ReactNode;
  /** Shows the account-setup steps (sign-up 1, verification 2). */
  step?: 1 | 2 | 3;
}) {
  const panel = useRef<HTMLDivElement>(null);
  // The form panel settles in once: heading, text, form, footer (never on re-render).
  useEntrance(panel, { max: 5, y: 10 });
  return (
    <div className="min-h-dvh bg-canvas lg:grid lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <aside className="relative hidden overflow-hidden bg-nav lg:flex lg:flex-col lg:justify-between lg:p-10 xl:p-14">
        <div className="relative flex items-center justify-between">
          <Brand light />
        </div>
        <div className="relative max-w-md">
          <p className="text-[28px] font-semibold leading-tight tracking-[-0.015em] text-white">
            The right message, to the right person, on the right channel, at the right moment.
          </p>
          <ul className="mt-8 space-y-4">
            {ASSURANCES.map(({ icon: Icon, text }) => (
              <li key={text} className="flex gap-3 text-[15px] leading-6 text-nav-ink">
                <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-white/5 text-nav-indicator ring-1 ring-inset ring-white/10">
                  <Icon className="h-4 w-4" aria-hidden />
                </span>
                {text}
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-nav-ink-muted">
          Proof of concept. All people and records are synthetic. A communication-decision tool, not a clinical one.
        </p>
      </aside>

      <main className="relative flex min-h-dvh flex-col px-4 py-6 sm:px-8 sm:py-10 lg:min-h-0 lg:justify-center lg:py-12">
        <div className="flex items-center justify-between gap-4 lg:absolute lg:right-6 lg:top-6">
          <span className="lg:hidden">
            <Brand />
          </span>
          <ThemeToggle />
        </div>
        <div ref={panel} className={cx("mx-auto w-full flex-1 pt-8 lg:flex-none lg:pt-0", wide ? "max-w-2xl" : "max-w-[420px]")}>
          {step && <SetupSteps step={step} />}
          <h1 className="text-[26px] font-semibold leading-8 tracking-[-0.015em] text-ink">{title}</h1>
          {subtitle && <p className="mt-2 text-[15px] leading-6 text-ink-muted">{subtitle}</p>}
          <div className="mt-8">{children}</div>
          {footer && <div className="mt-8 border-t border-line pt-6 text-sm text-ink-muted">{footer}</div>}
        </div>
        <div className="mx-auto mt-10 flex w-full max-w-2xl flex-col items-center gap-2 lg:mt-12">
          <LegalLinks className="justify-center" />
          <p className="text-center text-xs text-ink-subtle lg:hidden">
            Synthetic demonstration data. A communication-decision tool, not a clinical one.
          </p>
        </div>
      </main>
    </div>
  );
}
