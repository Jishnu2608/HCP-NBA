import { Activity, Lock, ShieldCheck, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cx } from "../ui";

/** The product mark: a pulse line in a rounded eucalyptus tile. */
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

/** Shared frame for the sign-in, sign-up and verification pages. */
export default function AuthLayout({
  title,
  subtitle,
  children,
  wide = false,
  footer,
}: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  wide?: boolean;
  footer?: ReactNode;
}) {
  return (
    <div className="min-h-dvh bg-canvas lg:grid lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <aside className="relative hidden overflow-hidden bg-nav lg:flex lg:flex-col lg:justify-between lg:p-10 xl:p-14">
        <div
          aria-hidden
          className="pointer-events-none absolute -right-32 -top-32 h-96 w-96 rounded-full bg-primary/40 blur-3xl"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-40 -left-24 h-96 w-96 rounded-full bg-accent/15 blur-3xl"
        />
        <div className="relative">
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

      <main className="flex min-h-dvh flex-col px-4 py-6 sm:px-8 sm:py-10 lg:min-h-0 lg:justify-center lg:py-12">
        <div className="lg:hidden">
          <Brand />
        </div>
        <div className={cx("mx-auto w-full flex-1 pt-8 lg:flex-none lg:pt-0", wide ? "max-w-2xl" : "max-w-[420px]")}>
          <h1 className="text-[26px] font-semibold leading-8 tracking-[-0.015em] text-ink">{title}</h1>
          {subtitle && <p className="mt-2 text-[15px] leading-6 text-ink-muted">{subtitle}</p>}
          <div className="mt-8">{children}</div>
          {footer && <div className="mt-8 border-t border-line pt-6 text-sm text-ink-muted">{footer}</div>}
        </div>
        <p className="mx-auto mt-10 max-w-md text-center text-xs text-ink-subtle lg:hidden">
          Synthetic demonstration data. A communication-decision tool, not a clinical one.
        </p>
      </main>
    </div>
  );
}
