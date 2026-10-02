// Shared presentational pieces. Small on purpose: one visual language across every role.
import { AlertTriangle, CheckCircle2, Clock, Loader2, ShieldAlert, XCircle } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

export function Card({
  title,
  action,
  children,
  className,
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("rounded-xl border border-stone-200 bg-white shadow-sm", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-stone-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-stone-800">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-stone-900">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-stone-500">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

type Variant = "primary" | "secondary" | "danger" | "ghost";
const VARIANT: Record<Variant, string> = {
  primary: "bg-brand-600 text-white hover:bg-brand-700 disabled:bg-stone-300",
  secondary:
    "border border-stone-300 bg-white text-stone-700 hover:bg-stone-50 disabled:text-stone-400",
  danger: "border border-red-200 bg-white text-red-700 hover:bg-red-50 disabled:text-stone-400",
  ghost: "text-stone-600 hover:bg-stone-100",
};

export function Button({
  variant = "secondary",
  busy,
  children,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium",
        "transition-colors disabled:cursor-not-allowed",
        VARIANT[variant],
        className,
      )}
    >
      {busy && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}

type Tone = "neutral" | "good" | "warn" | "bad" | "info" | "brand";
const TONE: Record<Tone, string> = {
  neutral: "bg-stone-100 text-stone-700 ring-stone-200",
  good: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  warn: "bg-amber-50 text-amber-800 ring-amber-200",
  bad: "bg-red-50 text-red-800 ring-red-200",
  info: "bg-sky-50 text-sky-800 ring-sky-200",
  brand: "bg-brand-50 text-brand-700 ring-brand-100",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        TONE[tone],
      )}
    >
      {children}
    </span>
  );
}

const STATUS: Record<string, { tone: Tone; label: string; icon: ReactNode }> = {
  ready_for_review: { tone: "info", label: "Ready for review", icon: <Clock className="h-3 w-3" /> },
  approved: { tone: "brand", label: "Approved", icon: <CheckCircle2 className="h-3 w-3" /> },
  sent: { tone: "neutral", label: "Sent", icon: <CheckCircle2 className="h-3 w-3" /> },
  responded: { tone: "good", label: "Responded", icon: <CheckCircle2 className="h-3 w-3" /> },
  blocked: { tone: "bad", label: "Blocked", icon: <ShieldAlert className="h-3 w-3" /> },
  rejected: { tone: "neutral", label: "Rejected", icon: <XCircle className="h-3 w-3" /> },
  expired: { tone: "neutral", label: "Superseded", icon: <Clock className="h-3 w-3" /> },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { tone: "neutral" as Tone, label: status, icon: null };
  return (
    <Badge tone={s.tone}>
      {s.icon}
      {s.label}
    </Badge>
  );
}

const RISK: Record<string, Tone> = { high: "bad", medium: "warn", low: "good" };

export function SegmentBadge({ value }: { value: string | null | undefined }) {
  if (!value) return null;
  if (value in RISK) return <Badge tone={RISK[value]}>{titleCase(value)} risk</Badge>;
  return <Badge tone={value.startsWith("high") ? "brand" : "neutral"}>{titleCase(value)}</Badge>;
}

const MLR: Record<string, Tone> = { approved: "good", pending: "warn", rejected: "bad" };

export function MlrBadge({ status, expired }: { status: string; expired?: boolean }) {
  if (expired) return <Badge tone="bad">Approval expired</Badge>;
  return <Badge tone={MLR[status] ?? "neutral"}>MLR {status}</Badge>;
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-stone-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-stone-500">{label}</div>
      <div className="tabular mt-1 text-2xl font-semibold text-stone-900">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-stone-500">{hint}</div>}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-8 text-sm text-stone-500">
      <Loader2 className="h-4 w-4 animate-spin" /> {label}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-2 py-8 text-center text-sm text-stone-500">{children}</div>;
}

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-stone-200 text-xs uppercase tracking-wide text-stone-500">
            {head.map((h, i) => (
              <th key={i} className="px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-stone-100">{children}</tbody>
      </table>
    </div>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-stone-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-stone-900">{children ?? "—"}</dd>
    </div>
  );
}

const CHANNEL_NAME: Record<string, string> = {
  sms: "Text message",
  email: "Email",
  portal: "Portal message",
  phone: "Phone call",
  rep_visit: "In-person visit",
};

export function channelName(channel: string | null | undefined) {
  return channel ? (CHANNEL_NAME[channel] ?? titleCase(channel)) : "—";
}

/** Audit event names for people: nba_sent becomes Recommendation sent. */
export function eventLabel(action: string) {
  return titleCase(action.replace(/^nba_/, "recommendation_"));
}

export function titleCase(text: string) {
  const spaced = text.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function pct(value: number | null | undefined, digits = 0) {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;
}

export function fmtDate(value: string | null | undefined) {
  if (!value) return "—";
  const d = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function fmtDateTime(value: string | null | undefined) {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
