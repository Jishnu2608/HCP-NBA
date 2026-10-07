// The product's design system: every page is built from these pieces, so all six roles
// share one visual language. Colours come only from the semantic tokens in index.css.
//
// Shape rule: controls (buttons, inputs, segmented controls) are 8px (rounded-lg); cards and
// panels are 12px (rounded-xl); badges and chips are 6px (rounded-md). Touch targets are at
// least 40px tall for primary controls.
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CalendarX2,
  CheckCheck,
  CheckCircle2,
  ChevronDown,
  CircleCheck,
  Clock3,
  CloudOff,
  Compass,
  FileWarning,
  Home,
  LayoutDashboard,
  LogIn,
  RotateCw,
  ServerCrash,
  ShieldX,
  Eye,
  EyeOff,
  History as HistoryIcon,
  Inbox,
  Loader2,
  Mail,
  MessageSquare,
  Phone,
  Search,
  Send,
  ShieldAlert,
  ShieldCheck,
  Smartphone,
  UserRound,
  X,
  XCircle,
} from "lucide-react";
import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";
import { Children, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "./api";
import { useAuth } from "./auth";

export function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

/* ------------------------------------------------------------------ layout */

export function Card({
  title,
  description,
  action,
  children,
  className,
  flush = false,
  id,
  dataAttr,
}: {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  /** No inner padding: for tables and lists that run edge to edge. */
  flush?: boolean;
  id?: string;
  /** Adds data-<name> to the section, for scroll targets. */
  dataAttr?: string;
}) {
  const headingId = useId();
  return (
    <section
      id={id}
      {...(dataAttr ? { [`data-${dataAttr}`]: "" } : {})}
      aria-labelledby={title ? headingId : undefined}
      className={cx("min-w-0 scroll-mt-24 rounded-xl border border-line bg-surface shadow-card", className)}
    >
      {(title || action) && (
        <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-5 pt-4 pb-3 sm:px-6">
          <div className="min-w-0">
            {title && (
              <h2 id={headingId} className="text-[15px] font-semibold leading-6 text-ink">
                {title}
              </h2>
            )}
            {description && <p className="mt-0.5 text-sm text-ink-subtle">{description}</p>}
          </div>
          {action && <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div>}
        </header>
      )}
      <div className={cx(flush ? "pb-1" : "px-5 pb-5 sm:px-6", !title && !action && !flush && "pt-5")}>
        {children}
      </div>
    </section>
  );
}

export function PageHeader({
  title,
  subtitle,
  action,
  back,
  meta,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  /** Show a Back link above the title. */
  back?: boolean;
  /** Badges or facts shown beside the title. */
  meta?: ReactNode;
}) {
  return (
    <header className="mb-6">
      {back && <BackLink />}
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 max-w-3xl">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <h1 className="text-2xl font-semibold tracking-[-0.015em] text-ink sm:text-[28px] sm:leading-9">
              {title}
            </h1>
            {meta}
          </div>
          {subtitle && <p className="mt-1.5 text-[15px] leading-6 text-ink-muted">{subtitle}</p>}
        </div>
        {action && <div className="flex flex-wrap items-center gap-2">{action}</div>}
      </div>
    </header>
  );
}

export function BackLink({ label = "Back" }: { label?: string }) {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      onClick={() => navigate(-1)}
      className="mb-3 -ml-1 inline-flex min-h-9 items-center gap-1.5 rounded-lg px-1 text-sm font-medium text-ink-subtle transition-colors hover:text-ink"
    >
      <ArrowLeft className="h-4 w-4" aria-hidden /> {label}
    </button>
  );
}

/** Small section label inside a card. Sentence case, not uppercase. */
export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return <h3 className={cx("text-[13px] font-semibold text-ink-muted", className)}>{children}</h3>;
}

export function Divider({ className }: { className?: string }) {
  return <hr className={cx("border-line", className)} />;
}

/* ----------------------------------------------------------------- buttons */

type Variant = "primary" | "accent" | "secondary" | "ghost" | "danger" | "quiet-danger";
type Size = "sm" | "md" | "lg";
const VARIANT: Record<Variant, string> = {
  primary: "bg-primary text-on-primary shadow-card hover:bg-primary-hover active:bg-primary-active",
  accent: "bg-accent text-on-accent shadow-card hover:bg-accent-hover",
  secondary: "border border-line-strong bg-surface text-ink shadow-card hover:bg-subtle",
  ghost: "text-ink-muted hover:bg-subtle hover:text-ink",
  danger: "bg-bad-fill text-ink-inverse shadow-card hover:opacity-90",
  "quiet-danger": "border border-bad-line bg-surface text-bad hover:bg-bad-soft",
};
const SIZE: Record<Size, string> = {
  sm: "min-h-9 px-3 text-[13px] gap-1.5",
  md: "min-h-10 px-4 text-sm gap-2",
  lg: "min-h-12 px-5 text-[15px] gap-2",
};

export function Button({
  variant = "secondary",
  size = "md",
  busy,
  children,
  className,
  type = "button",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size; busy?: boolean }) {
  return (
    <button
      {...rest}
      type={type}
      aria-busy={busy || undefined}
      disabled={rest.disabled || busy}
      className={cx(
        "inline-flex select-none items-center justify-center whitespace-nowrap rounded-lg font-semibold",
        "transition-[background-color,color,box-shadow,transform,opacity] duration-150 active:translate-y-px",
        "disabled:pointer-events-none disabled:opacity-50 disabled:shadow-none",
        VARIANT[variant],
        SIZE[size],
        className,
      )}
    >
      {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function IconButton({
  label,
  children,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...rest}
      className={cx(
        "inline-grid h-10 w-10 shrink-0 place-items-center rounded-lg text-ink-muted transition-colors hover:bg-subtle hover:text-ink",
        className,
      )}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ badges */

export type Tone = "neutral" | "ok" | "warn" | "bad" | "info" | "brand" | "accent" | "sage";
const TONE: Record<Tone, string> = {
  neutral: "bg-neutral-soft text-neutral ring-neutral-line",
  ok: "bg-ok-soft text-ok ring-ok-line",
  warn: "bg-warn-soft text-warn ring-warn-line",
  bad: "bg-bad-soft text-bad ring-bad-line",
  info: "bg-info-soft text-info ring-info-line",
  brand: "bg-primary-soft text-primary-ink ring-primary-line",
  accent: "bg-accent-soft text-accent-ink ring-accent-soft",
  sage: "bg-sage text-sage-ink ring-sage",
};

export function Badge({
  tone = "neutral",
  icon,
  children,
  className,
}: {
  tone?: Tone;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex max-w-full items-start gap-1 rounded-md px-1.5 py-0.5 text-left text-xs font-semibold leading-5 ring-1 ring-inset",
        "[&>svg]:mt-[3px]",
        TONE[tone],
        className,
      )}
    >
      {icon}
      <span className="min-w-0 [overflow-wrap:anywhere]">{children}</span>
    </span>
  );
}

const ic = "h-3.5 w-3.5 shrink-0";

/** Recommendation lifecycle. Each state has its own colour, icon and words. */
export const NBA_STATUS: Record<string, { tone: Tone; label: string; icon: ReactNode }> = {
  ready_for_review: { tone: "info", label: "Pending review", icon: <Clock3 className={ic} aria-hidden /> },
  approved: { tone: "brand", label: "Approved", icon: <CheckCircle2 className={ic} aria-hidden /> },
  sent: { tone: "sage", label: "Sent", icon: <Send className={ic} aria-hidden /> },
  responded: { tone: "ok", label: "Responded", icon: <CheckCheck className={ic} aria-hidden /> },
  blocked: { tone: "bad", label: "Blocked", icon: <ShieldAlert className={ic} aria-hidden /> },
  rejected: { tone: "neutral", label: "Rejected", icon: <XCircle className={ic} aria-hidden /> },
  expired: { tone: "neutral", label: "Superseded", icon: <HistoryIcon className={ic} aria-hidden /> },
};

export function StatusBadge({ status }: { status: string }) {
  const s = NBA_STATUS[status] ?? { tone: "neutral" as Tone, label: titleCase(status), icon: null };
  return (
    <span key={status} className="animate-pop inline-flex max-w-full">
      <Badge tone={s.tone} icon={s.icon}>
        {s.label}
      </Badge>
    </span>
  );
}

/** Adherence risk. High / medium / low always carry an icon and the word "risk". */
export const RISK: Record<string, { tone: Tone; icon: ReactNode; label: string }> = {
  high: { tone: "bad", icon: <AlertTriangle className={ic} aria-hidden />, label: "High risk" },
  medium: { tone: "warn", icon: <AlertCircle className={ic} aria-hidden />, label: "Medium risk" },
  low: { tone: "ok", icon: <CircleCheck className={ic} aria-hidden />, label: "Low risk" },
};

export function SegmentBadge({ value }: { value: string | null | undefined }) {
  if (!value) return null;
  const risk = RISK[value];
  if (risk) {
    return (
      <Badge tone={risk.tone} icon={risk.icon}>
        {risk.label}
      </Badge>
    );
  }
  // Invited HCPs: prescribing volume is not recorded, so no value tier is shown.
  const UNRATED: Record<string, string> = {
    unrated_new: "New contact",
    unrated_engaged: "Engaged · volume not rated",
    unrated_dormant: "Rarely engages · volume not rated",
  };
  if (UNRATED[value]) return <Badge tone="neutral">{UNRATED[value]}</Badge>;
  return <Badge tone={value.startsWith("high") ? "brand" : "sage"}>{titleCase(value)}</Badge>;
}

export function MlrBadge({ status, expired }: { status: string; expired?: boolean }) {
  if (expired) {
    return (
      <Badge tone="bad" icon={<CalendarX2 className={ic} aria-hidden />}>
        Approval expired
      </Badge>
    );
  }
  if (status === "approved") {
    return (
      <Badge tone="ok" icon={<ShieldCheck className={ic} aria-hidden />}>
        MLR approved
      </Badge>
    );
  }
  if (status === "pending") {
    return (
      <Badge tone="warn" icon={<Clock3 className={ic} aria-hidden />}>
        In MLR review
      </Badge>
    );
  }
  if (status === "rejected") {
    return (
      <Badge tone="bad" icon={<XCircle className={ic} aria-hidden />}>
        MLR rejected
      </Badge>
    );
  }
  const other: Record<string, [Tone, string]> = {
    draft: ["neutral", "Draft"],
    changes_requested: ["warn", "Changes requested"],
    withdrawn: ["bad", "Withdrawn"],
    superseded: ["neutral", "Superseded"],
  };
  const [tone, text] = other[status] ?? ["neutral", `MLR ${status}`];
  return <Badge tone={tone}>{text}</Badge>;
}

export function ConsentBadge({ granted }: { granted: boolean }) {
  return granted ? (
    <Badge tone="ok" icon={<CheckCircle2 className={ic} aria-hidden />}>
      Granted
    </Badge>
  ) : (
    <Badge tone="bad" icon={<XCircle className={ic} aria-hidden />}>
      Not granted
    </Badge>
  );
}

/* ----------------------------------------------------------------- figures */

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/**
 * A figure that counts up to its value when it first appears or changes ("1,087",
 * "72.2%", "16 days"). Prefix, suffix, grouping and decimals are kept. Anything that is not
 * a plain figure is shown as it is. No animation under reduced motion.
 */
export function AnimatedNumber({ value }: { value: ReactNode }) {
  const text = typeof value === "number" ? value.toLocaleString("en-US") : typeof value === "string" ? value : null;
  const match = text?.match(/^(\D*?)(\d[\d,]*(?:\.\d+)?)(.*)$/s) ?? null;
  const target = match ? Number(match[2].replace(/,/g, "")) : NaN;
  const decimals = match?.[2].split(".")[1]?.length ?? 0;
  const grouped = match?.[2].includes(",") ?? false;
  const [shown, setShown] = useState(target);
  const from = useRef(0);

  useEffect(() => {
    if (Number.isNaN(target)) return;
    if (reducedMotion()) {
      setShown(target);
      return;
    }
    const start = performance.now();
    const origin = from.current;
    let frame = 0;
    const step = (now: number) => {
      const k = Math.min(1, (now - start) / 650);
      const eased = 1 - (1 - k) ** 3;
      setShown(origin + (target - origin) * eased);
      if (k < 1) frame = requestAnimationFrame(step);
      else from.current = target;
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target]);

  if (!match || Number.isNaN(target)) return <>{value}</>;
  // Before the first frame (the value was "—" while loading) there is no figure yet: start
  // from where the count starts, never from NaN.
  const current = Number.isFinite(shown) ? shown : from.current;
  const number = grouped
    ? current.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
    : current.toFixed(decimals);
  return (
    <>
      {/* Screen readers get the final value once, not every frame. */}
      <span aria-hidden>
        {match[1]}
        {number}
        {match[3]}
      </span>
      <span className="sr-only">{text}</span>
    </>
  );
}

type KpiTone = "ok" | "warn" | "bad" | "brand";
const KPI_VALUE: Record<KpiTone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  brand: "text-primary-ink",
};
const KPI_ICON: Record<KpiTone | "none", string> = {
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
  brand: "bg-primary-soft text-primary-ink",
  none: "bg-subtle text-ink-subtle",
};

/**
 * Key-figure card. Every KPI in the product uses this one structure so tiles in a row
 * line up regardless of text length:
 *   title region  - always two lines tall (longer titles are clamped, full text in a tooltip)
 *   value         - one line, large tabular figures, counts up on first view
 *   hint region   - always one line tall
 * The icon sits in a fixed 32px tile, top right. With `to`, the whole card is a link with a
 * hover lift and an arrow that appears on hover or focus.
 */
export function KpiCard({
  label,
  value,
  hint,
  tone,
  icon,
  to,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: KpiTone;
  icon?: ReactNode;
  /** Makes the card a link to the detail behind the figure. */
  to?: string;
  className?: string;
}) {
  const body = (
    <>
      <div className="flex items-start gap-3">
        <p title={label} className="line-clamp-2 min-h-10 flex-1 text-[13px] font-medium leading-5 text-ink-muted">
          {label}
        </p>
        {icon && (
          <span
            aria-hidden
            className={cx(
              "grid h-8 w-8 shrink-0 place-items-center rounded-lg transition-transform duration-200 [&>svg]:h-4 [&>svg]:w-4",
              KPI_ICON[tone ?? "none"],
              to && "group-hover:-translate-y-0.5",
            )}
          >
            {icon}
          </span>
        )}
      </div>
      <div
        className={cx(
          "tabular mt-2 truncate text-[28px] font-semibold leading-9 tracking-[-0.02em]",
          tone ? KPI_VALUE[tone] : "text-ink",
        )}
      >
        <AnimatedNumber value={value} />
      </div>
      <div className="mt-1 flex min-h-4 items-center justify-between gap-2">
        <p title={hint} className="line-clamp-1 text-xs leading-4 text-ink-subtle">
          {hint ?? "\u00a0"}
        </p>
        {to && (
          <ArrowRight
            aria-hidden
            className="h-3.5 w-3.5 shrink-0 -translate-x-1 text-primary-ink opacity-0 transition-all duration-200 group-hover:translate-x-0 group-hover:opacity-100 group-focus-visible:translate-x-0 group-focus-visible:opacity-100"
          />
        )}
      </div>
    </>
  );
  const shell = cx(
    "group flex h-full min-w-0 flex-col rounded-xl border border-line bg-surface p-4 shadow-card sm:p-5",
    to && "lift",
    className,
  );
  return to ? (
    <Link to={to} className={shell} aria-label={`${label}: ${typeof value === "string" || typeof value === "number" ? value : ""}${hint ? `, ${hint}` : ""}`}>
      {body}
    </Link>
  ) : (
    <div className={shell}>{body}</div>
  );
}

/** @deprecated name kept for existing call sites; same component as KpiCard. */
export const Stat = KpiCard;

/**
 * Row of KPI cards. The column count follows the number of cards, so every count has an
 * intentional layout with no empty cells: 3 -> 1/3; 4 -> 2/4; 5 -> 2/(3+2)/5; 6 -> 2/3/6.
 * On two-column phones an odd last card spans the full width.
 */
export function KpiGrid({ children, className }: { children: ReactNode; className?: string }) {
  const items = Children.toArray(children).filter(Boolean);
  const n = items.length;
  const grid =
    n <= 3
      ? "grid-cols-1 sm:grid-cols-3"
      : n === 4
        ? "grid-cols-2 lg:grid-cols-4"
        : n === 5
          ? "grid-cols-2 md:grid-cols-6 xl:grid-cols-5"
          : "grid-cols-2 md:grid-cols-3 xl:grid-cols-6";
  const span = (i: number) => {
    if (n === 5) return cx(i < 3 ? "md:col-span-2" : "md:col-span-3", "xl:col-span-1", i === 4 && "col-span-2");
    if (n > 3 && n % 2 === 1 && i === n - 1) return "col-span-2 md:col-span-1";
    return undefined;
  };
  return (
    <div className={cx("stagger mb-8 grid gap-4", grid, className)}>
      {items.map((item, i) => (
        <div key={i} className={cx("min-w-0", span(i))}>
          {item}
        </div>
      ))}
    </div>
  );
}

/** Horizontal proportion bar, 0..1. */
export function Meter({
  value,
  tone = "brand",
  label,
  className,
}: {
  value: number | null | undefined;
  tone?: "brand" | "ok" | "warn" | "bad" | "neutral";
  label?: string;
  className?: string;
}) {
  const fill = {
    brand: "bg-primary",
    ok: "bg-ok-fill",
    warn: "bg-warn-fill",
    bad: "bg-bad-fill",
    neutral: "bg-line-strong",
  };
  const v = Math.max(0, Math.min(1, value ?? 0));
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(v * 100)}
      className={cx("h-2 overflow-hidden rounded-full bg-sunken", className)}
    >
      <div
        className={cx("animate-grow h-full origin-left rounded-full transition-[width] duration-500", fill[tone])}
        style={{ width: `${v * 100}%` }}
      />
    </div>
  );
}

export function Field({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[13px] text-ink-subtle">{label}</dt>
      <dd className="mt-0.5 break-words text-sm font-medium text-ink">{children ?? "—"}</dd>
    </div>
  );
}

/** Text that may be long: truncated on one line, full text in the native tooltip. */
export function Truncate({ children, className }: { children: string | null | undefined; className?: string }) {
  if (!children) return <span className={className}>—</span>;
  return (
    <span title={children} className={cx("block truncate", className)}>
      {children}
    </span>
  );
}

export function Avatar({ name, size = "md" }: { name: string; size?: "sm" | "md" | "lg" }) {
  const initials = name
    .replace(/^(Dr\.?|Mr\.?|Ms\.?|Mrs\.?)\s+/i, "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
  const dims = { sm: "h-8 w-8 text-xs", md: "h-10 w-10 text-sm", lg: "h-14 w-14 text-lg" };
  return (
    <span
      aria-hidden
      className={cx(
        "inline-grid shrink-0 place-items-center rounded-[10px] bg-primary-soft font-semibold text-primary-ink ring-1 ring-inset ring-primary-line",
        dims[size],
      )}
    >
      {initials || <UserRound className="h-4 w-4" />}
    </span>
  );
}

/** The professional verification mark. Shown only when the server says the account is
 *  professionally verified (an authorised invitation completed, or platform-provisioned
 *  staff); never derived from the role. Small blue disc, white check, label on hover/focus. */
export function VerifiedBadge({
  source,
  size = "sm",
  className,
}: {
  source?: "invitation" | "system" | null;
  size?: "sm" | "md";
  className?: string;
}) {
  const label =
    source === "system"
      ? "Verified professional — provisioned by the platform"
      : "Verified professional — onboarded through an authorized invitation";
  const px = size === "md" ? 18 : 15;
  return (
    <span
      role="img"
      aria-label={label}
      tabIndex={0}
      className={cx(
        "group relative inline-flex shrink-0 rounded-full align-[-2px] focus:outline-none focus-visible:ring-[3px] focus-visible:ring-primary/30",
        className,
      )}
    >
      <svg width={px} height={px} viewBox="0 0 16 16" aria-hidden focusable="false">
        <circle cx="8" cy="8" r="8" fill="var(--primary)" />
        <path d="M4.6 8.3l2.2 2.2 4.6-4.8" fill="none" stroke="#ffffff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 hidden w-max max-w-[240px] -translate-x-1/2 rounded-md bg-nav px-2.5 py-1.5 text-left text-xs font-medium leading-snug text-nav-ink shadow-lg group-hover:block group-focus-visible:block"
      >
        {label}
      </span>
    </span>
  );
}

/** Name followed by the verification mark when the server reports it. */
export function PersonName({
  name,
  verified,
  source,
  className,
}: {
  name: ReactNode;
  verified?: boolean;
  source?: "invitation" | "system" | null;
  className?: string;
}) {
  return (
    <span className={cx("inline-flex min-w-0 items-center gap-1.5", className)}>
      <span className="min-w-0 truncate">{name}</span>
      {verified && <VerifiedBadge source={source} />}
    </span>
  );
}

/* ------------------------------------------------------- states and alerts */

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx("skeleton rounded-md", className)} />;
}

/** Loading placeholder shaped like a page: header, figures, a panel of rows. */
export function Loading({ label = "Loading", rows = 6 }: { label?: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" className="animate-fade">
      <span className="sr-only">{label}</span>
      <Skeleton className="h-7 w-56" />
      <Skeleton className="mt-3 h-4 w-80 max-w-full" />
      <div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-[92px] rounded-xl" />
        ))}
      </div>
      <div className="mt-5 space-y-3 rounded-xl border border-line bg-surface p-5">
        {Array.from({ length: rows }, (_, i) => (
          <Skeleton key={i} className="h-10" />
        ))}
      </div>
    </div>
  );
}

/** Loading placeholder inside a panel. */
export function LoadingRows({ rows = 5, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-live="polite" className="space-y-2.5 py-1">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-10" />
      ))}
    </div>
  );
}

export function Alert({
  tone = "info",
  title,
  children,
  icon,
  className,
  action,
}: {
  tone?: "info" | "ok" | "warn" | "bad";
  title?: ReactNode;
  children?: ReactNode;
  icon?: ReactNode;
  className?: string;
  action?: ReactNode;
}) {
  const style = {
    info: "border-info-line bg-info-soft text-info",
    ok: "border-ok-line bg-ok-soft text-ok",
    warn: "border-warn-line bg-warn-soft text-warn",
    bad: "border-bad-line bg-bad-soft text-bad",
  };
  const fallback = {
    info: <AlertCircle className="h-5 w-5" aria-hidden />,
    ok: <CheckCircle2 className="h-5 w-5" aria-hidden />,
    warn: <AlertTriangle className="h-5 w-5" aria-hidden />,
    bad: <ShieldAlert className="h-5 w-5" aria-hidden />,
  };
  return (
    <div
      role={tone === "bad" || tone === "warn" ? "alert" : "status"}
      className={cx("flex items-start gap-3 rounded-xl border px-4 py-3", style[tone], className)}
    >
      <span className="mt-px shrink-0">{icon ?? fallback[tone]}</span>
      <div className="min-w-0 flex-1 text-sm leading-6">
        {title && <div className="font-semibold">{title}</div>}
        {children && <div className={cx(Boolean(title) && "text-ink-muted")}>{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------- errors */

export type ErrorKind = "not_found" | "bad_request" | "unauthenticated" | "forbidden" | "server" | "data";

/** One set of words and icons per kind of failure, shared by every error screen. */
export const ERROR_COPY: Record<
  ErrorKind,
  { code: string | null; title: string; message: string; icon: typeof AlertTriangle; tone: "neutral" | "warn" | "bad" | "info" }
> = {
  not_found: {
    code: "404",
    title: "Page not found",
    message: "The page you're looking for doesn't exist or may have moved.",
    icon: Compass,
    tone: "neutral",
  },
  bad_request: {
    code: "400",
    title: "This request isn't valid",
    message:
      "The link or the information sent is incomplete or not in the expected form. Check it, or start again from your workspace.",
    icon: FileWarning,
    tone: "warn",
  },
  unauthenticated: {
    code: "401",
    title: "Please sign in",
    message: "You're not signed in, or your session has ended. Sign in to continue.",
    icon: LogIn,
    tone: "info",
  },
  forbidden: {
    code: "403",
    title: "Access restricted",
    message: "You don't have permission to access this resource. Access is decided by your account on the server.",
    icon: ShieldX,
    tone: "bad",
  },
  server: {
    code: "500",
    title: "Something went wrong",
    message: "We couldn't complete that request. Please try again.",
    icon: ServerCrash,
    tone: "bad",
  },
  data: {
    code: null,
    title: "Unable to load this information",
    message: "Something prevented us from retrieving the requested data. Check your connection and try again.",
    icon: CloudOff,
    tone: "warn",
  },
};

/** Classifies any thrown value. Never looks at stack traces or server internals. */
export function errorKind(error: unknown): ErrorKind {
  if (error instanceof ApiError) {
    if (error.status === 0) return "data";
    if (error.status === 401) return "unauthenticated";
    if (error.status === 403) return "forbidden";
    if (error.status === 404) return "not_found";
    if (error.status >= 500) return "server";
    return "bad_request";
  }
  return "server";
}

/**
 * Text that is safe to show for an error. The API's own 4xx messages are written for
 * people (for example "Patient has not consented to outreach on this channel") and are
 * shown as they are; server failures, network failures, validation dumps and unexpected
 * exceptions get plain-language text instead, so nothing internal reaches the screen.
 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "We couldn't reach the server. Check your connection and try again.";
    if (error.status >= 500) return ERROR_COPY.server.message;
    if (error.status === 422 && !error.code) return "Some of the information is missing or not in the expected form.";
    // The limit itself is enforced by the server; this is only its wording on screen.
    if (error.code === "rate_limited") return "Too many attempts. Please wait a few minutes and try again.";
    return error.message || ERROR_COPY[errorKind(error)].message;
  }
  // Errors raised deliberately by this app's own code carry readable messages; anything
  // else (TypeError and similar) is a bug and gets the generic text.
  if (error instanceof Error && error.constructor === Error && error.message) return error.message;
  return ERROR_COPY.server.message;
}

/** Inline error for a form or an action. Renders nothing when there is no error. */
export function ErrorNote({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null;
  return (
    <Alert tone="bad" icon={<AlertTriangle className="h-5 w-5" aria-hidden />} className={className}>
      <span className="text-bad">{errorMessage(error)}</span>
    </Alert>
  );
}

/**
 * The shared error layout: icon, status code, title, explanation and recovery actions.
 * `page` fills the content area (a route that cannot be shown); `section` sits inside a
 * card (one panel of a page failed to load).
 */
export function ErrorPanel({
  kind,
  title,
  message,
  onRetry,
  variant = "page",
}: {
  kind: ErrorKind;
  title?: string;
  message?: string;
  onRetry?: () => void;
  variant?: "page" | "section";
}) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const copy = ERROR_COPY[kind];
  const Icon = copy.icon;
  const page = variant === "page";
  const size = page ? "lg" : "md";
  const recoverable = kind === "server" || kind === "data";
  const canGoBack = window.history.length > 1;
  const iconTone = {
    neutral: "bg-subtle text-ink-muted",
    warn: "bg-warn-soft text-warn",
    bad: "bg-bad-soft text-bad",
    info: "bg-info-soft text-info",
  }[copy.tone];
  const HeadingTag = page ? "h1" : "h2";

  const actions: ReactNode[] = [];
  const next = () => (actions.length ? "secondary" : "primary");
  if (recoverable && onRetry) {
    actions.push(
      <Button key="retry" variant={next()} size={size} onClick={onRetry}>
        <RotateCw className="h-4 w-4" aria-hidden /> Try again
      </Button>,
    );
  }
  if (kind === "unauthenticated") {
    actions.push(
      <Button key="signin" variant={next()} size={size} onClick={() => navigate("/login", { state: { from: location.pathname } })}>
        <LogIn className="h-4 w-4" aria-hidden /> Sign in
      </Button>,
    );
  } else if (!user) {
    actions.push(
      <Button key="home" variant={next()} size={size} onClick={() => navigate("/")}>
        <Home className="h-4 w-4" aria-hidden /> Return to home
      </Button>,
    );
  } else if (page || !recoverable) {
    // Signed in: recovery lands on the role's own dashboard, as the server defines it.
    actions.push(
      <Button key="dashboard" variant={next()} size={size} onClick={() => navigate(user.home)}>
        <LayoutDashboard className="h-4 w-4" aria-hidden /> Go to my dashboard
      </Button>,
    );
  }
  if (canGoBack && kind !== "unauthenticated" && page) {
    actions.push(
      <Button key="back" variant="ghost" size={size} onClick={() => navigate(-1)}>
        <ArrowLeft className="h-4 w-4" aria-hidden /> Go back
      </Button>,
    );
  }

  return (
    <div
      role={kind === "server" || kind === "forbidden" ? "alert" : "status"}
      className={cx("mx-auto flex max-w-lg flex-col items-center text-center", page ? "py-14 sm:py-20" : "px-5 py-10")}
    >
      <span className={cx("grid place-items-center", iconTone, page ? "h-14 w-14 rounded-2xl" : "h-11 w-11 rounded-xl")} aria-hidden>
        <Icon className={page ? "h-7 w-7" : "h-5 w-5"} />
      </span>
      {copy.code && <p className="tabular mt-5 text-[13px] font-semibold text-ink-subtle">Error {copy.code}</p>}
      <HeadingTag
        className={cx(
          "font-semibold tracking-[-0.015em] text-ink",
          copy.code ? "mt-1" : "mt-5",
          page ? "text-2xl sm:text-[28px] sm:leading-9" : "text-lg",
        )}
      >
        {title ?? copy.title}
      </HeadingTag>
      <p className={cx("mt-2 text-ink-muted", page ? "text-[15px] leading-6" : "text-sm leading-6")}>{message ?? copy.message}</p>
      {actions.length > 0 && (
        <div className="mt-7 flex w-full flex-col items-stretch justify-center gap-2 sm:w-auto sm:flex-row sm:flex-wrap sm:items-center">
          {actions}
        </div>
      )}
    </div>
  );
}

/**
 * For a query that failed: picks the kind from the error. `title` is used for data and
 * server failures ("Patients could not be loaded"); 404 / 403 / 400 keep their own words.
 * Pass `retry` (usually `query.refetch`) to offer Try again.
 */
export function ErrorState({
  error,
  title,
  retry,
  variant = "section",
}: {
  error: unknown;
  title?: string;
  retry?: () => void;
  variant?: "page" | "section";
}) {
  const kind = errorKind(error);
  const ownTitle = kind === "data" || kind === "server";
  const record = kind === "not_found";
  return (
    <ErrorPanel
      kind={kind}
      variant={variant}
      title={ownTitle ? title : record ? "Not found" : undefined}
      message={record ? "This record doesn't exist, or it isn't available to your account." : undefined}
      onRetry={retry}
    />
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
  tone = "neutral",
  compact = false,
}: {
  icon?: ReactNode;
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  tone?: "neutral" | "bad" | "ok";
  compact?: boolean;
}) {
  const iconTone = {
    neutral: "bg-subtle text-ink-subtle",
    bad: "bg-bad-soft text-bad",
    ok: "bg-ok-soft text-ok",
  };
  return (
    <div className={cx("flex flex-col items-center text-center", compact ? "px-4 py-6" : "px-6 py-12")}>
      <span className={cx("grid h-11 w-11 place-items-center rounded-xl", iconTone[tone])} aria-hidden>
        {icon ?? <Inbox className="h-5 w-5" />}
      </span>
      <p className="mt-3 text-[15px] font-semibold text-ink">{title}</p>
      {children && <p className="mt-1 max-w-md text-sm text-ink-subtle">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Kept for short inline empties. */
export function Empty({ children }: { children: ReactNode }) {
  return <EmptyState title={children} compact />;
}

/* ------------------------------------------------------------------ tables */

/** Simple table for small, fixed sets (a handful of rows). Scrolls sideways if it must. */
export function Table({
  head,
  children,
  caption,
  align,
}: {
  head: ReactNode[];
  children: ReactNode;
  caption?: string;
  /** Per-column alignment; numbers should be "right". */
  align?: Array<"left" | "right" | undefined>;
}) {
  return (
    <div className="scroll-quiet -mx-1 overflow-x-auto px-1">
      <table className="w-full border-collapse text-left text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr className="border-b border-line">
            {head.map((h, i) => (
              <th
                key={i}
                scope="col"
                className={cx(
                  "whitespace-nowrap px-3 py-2.5 text-[13px] font-medium text-ink-subtle first:pl-0 last:pr-0",
                  align?.[i] === "right" && "text-right",
                )}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line [&_td]:px-3 [&_td]:py-3 [&_td:first-child]:pl-0 [&_td:last-child]:pr-0">
          {children}
        </tbody>
      </table>
    </div>
  );
}

export interface Column<T> {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  className?: string;
  align?: "left" | "right";
  /** Leave out of the stacked mobile card. */
  hideOnMobile?: boolean;
  /** Use as the mobile card's title line (first such column). */
  primary?: boolean;
}

/**
 * Data table that becomes a stacked list of cards below 768px instead of squeezing columns.
 * Rows can be clickable; the click is also reachable by keyboard.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  rowClassName,
  caption,
  mobileAside,
  tableFrom = "3xl",
}: {
  /** Container width from which the table layout is used; below it, stacked cards. */
  tableFrom?: "2xl" | "3xl" | "4xl" | "5xl";
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string | false | undefined;
  caption: string;
  /** Right-hand element on each mobile card, for example a status badge. */
  mobileAside?: (row: T) => ReactNode;
}) {
  const primary = columns.find((c) => c.primary) ?? columns[0];
  const rest = columns.filter((c) => c !== primary && !c.hideOnMobile);
  const keyActivate = (row: T) => (e: React.KeyboardEvent) => {
    if (onRowClick && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      onRowClick(row);
    }
  };
  const show = TABLE_FROM[tableFrom];
  return (
    <div className="@container">
      <div className={cx("scroll-quiet hidden overflow-x-auto", show.table)}>
        <table className="w-full border-collapse text-left text-sm">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr className="border-b border-line bg-subtle/60">
              {columns.map((c) => (
                <th
                  key={c.key}
                  scope="col"
                  className={cx(
                    "whitespace-nowrap px-4 py-2.5 text-[13px] font-medium text-ink-subtle first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6",
                    c.align === "right" && "text-right",
                    c.className,
                  )}
                >
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((row) => (
              <tr
                key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                onKeyDown={onRowClick ? keyActivate(row) : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                className={cx(
                  "transition-colors",
                  onRowClick && "cursor-pointer hover:bg-subtle/70 focus-visible:bg-subtle",
                  rowClassName?.(row),
                )}
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={cx(
                      "px-4 py-3 align-top first:pl-5 last:pr-5 sm:first:pl-6 sm:last:pr-6",
                      c.align === "right" && "text-right",
                      c.className,
                    )}
                  >
                    {c.cell(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ul className={cx("divide-y divide-line", show.list)} aria-label={caption}>
        {rows.map((row) => (
          <li
            key={rowKey(row)}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
            onKeyDown={onRowClick ? keyActivate(row) : undefined}
            tabIndex={onRowClick ? 0 : undefined}
            className={cx("px-5 py-4", onRowClick && "cursor-pointer active:bg-subtle", rowClassName?.(row))}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">{primary.cell(row)}</div>
              {mobileAside && <div className="shrink-0">{mobileAside(row)}</div>}
            </div>
            {rest.length > 0 && (
              // Label above value on phones; label beside value once the card is wide enough.
              <dl className="mt-3 grid gap-y-2.5 text-sm @md:grid-cols-[minmax(0,9rem)_minmax(0,1fr)] @md:gap-x-4 @md:gap-y-2">
                {rest.map((c) => (
                  <div key={c.key} className="min-w-0 @md:contents">
                    <dt className="text-xs text-ink-subtle @md:text-sm">{c.header}</dt>
                    <dd className="mt-0.5 min-w-0 text-ink @md:mt-0 [&_.ml-auto]:ml-0">{c.cell(row)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// Literal class names so Tailwind generates them.
const TABLE_FROM = {
  "2xl": { table: "@2xl:block", list: "@2xl:hidden" },
  "3xl": { table: "@3xl:block", list: "@3xl:hidden" },
  "4xl": { table: "@4xl:block", list: "@4xl:hidden" },
  "5xl": { table: "@5xl:block", list: "@5xl:hidden" },
};

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  previousLabel = "Previous",
  nextLabel = "Next",
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  previousLabel?: string;
  nextLabel?: string;
}) {
  if (!total) return null;
  return (
    <nav
      aria-label="Pagination"
      className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-5 py-3 text-sm text-ink-subtle sm:px-6"
    >
      <span className="tabular">
        {(page * pageSize + 1).toLocaleString()}–{Math.min((page + 1) * pageSize, total).toLocaleString()} of{" "}
        {total.toLocaleString()}
      </span>
      <div className="flex gap-2">
        <Button size="sm" disabled={page === 0} onClick={() => onPage(page - 1)}>
          {previousLabel}
        </Button>
        <Button size="sm" disabled={(page + 1) * pageSize >= total} onClick={() => onPage(page + 1)}>
          {nextLabel}
        </Button>
      </div>
    </nav>
  );
}

/* ------------------------------------------------------------------- forms */

const control =
  "block w-full rounded-lg border border-line-strong bg-surface px-3 text-[15px] text-ink shadow-card " +
  "placeholder:text-ink-subtle transition-[border-color,box-shadow] " +
  "focus:border-primary focus:outline-none focus:ring-[3px] focus:ring-primary/20 " +
  "disabled:bg-subtle disabled:text-ink-subtle read-only:bg-subtle aria-[invalid=true]:border-bad aria-[invalid=true]:focus:ring-bad/20";

export function FormField({
  label,
  hint,
  error,
  required,
  children,
  htmlFor,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  children: ReactNode;
  htmlFor: string;
  className?: string;
}) {
  return (
    <div className={cx("min-w-0", className)}>
      <label htmlFor={htmlFor} className="mb-1.5 flex items-baseline gap-1 text-sm font-medium text-ink">
        {label}
        {required && (
          <span className="text-bad" aria-hidden>
            *
          </span>
        )}
      </label>
      {children}
      {error ? (
        <p id={`${htmlFor}-error`} className="mt-1.5 flex items-start gap-1.5 text-[13px] text-bad">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /> {error}
        </p>
      ) : hint ? (
        <p id={`${htmlFor}-hint`} className="mt-1.5 text-[13px] text-ink-subtle">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
};

export function TextField({ label, hint, error, className, id, ...input }: InputProps) {
  const auto = useId();
  const fieldId = id ?? auto;
  return (
    <FormField label={label} hint={hint} error={error} required={input.required} htmlFor={fieldId} className={className}>
      <input
        {...input}
        id={fieldId}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${fieldId}-error` : hint ? `${fieldId}-hint` : undefined}
        className={cx(control, "h-11")}
      />
    </FormField>
  );
}

export function PasswordField({ label, hint, error, className, id, ...input }: InputProps) {
  const auto = useId();
  const fieldId = id ?? auto;
  const [visible, setVisible] = useState(false);
  return (
    <FormField label={label} hint={hint} error={error} required={input.required} htmlFor={fieldId} className={className}>
      <div className="relative">
        <input
          {...input}
          id={fieldId}
          type={visible ? "text" : "password"}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${fieldId}-error` : hint ? `${fieldId}-hint` : undefined}
          className={cx(control, "h-11 pr-12")}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
          className="absolute inset-y-0 right-0 grid w-11 place-items-center rounded-r-lg text-ink-subtle hover:text-ink"
        >
          {visible ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
        </button>
      </div>
    </FormField>
  );
}

export function TextArea({
  label,
  hint,
  error,
  className,
  id,
  ...area
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: ReactNode; hint?: ReactNode; error?: ReactNode }) {
  const auto = useId();
  const fieldId = id ?? auto;
  return (
    <FormField label={label} hint={hint} error={error} required={area.required} htmlFor={fieldId} className={className}>
      <textarea
        {...area}
        id={fieldId}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${fieldId}-error` : hint ? `${fieldId}-hint` : undefined}
        className={cx(control, "py-2.5 leading-relaxed")}
      />
    </FormField>
  );
}

/** Unlabelled-looking controls in toolbars still carry an accessible name. */
export function Select({
  label,
  className,
  children,
  ...select
}: SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  return (
    <div className={cx("relative min-w-0", className)}>
      <select
        {...select}
        aria-label={label}
        className={cx(control, "h-10 appearance-none truncate pr-9 text-sm font-medium")}
      >
        {children}
      </select>
      <ChevronDown
        className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-subtle"
        aria-hidden
      />
    </div>
  );
}

export function SearchInput({
  label,
  className,
  ...input
}: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <div className={cx("relative min-w-0", className)}>
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-subtle" aria-hidden />
      <input {...input} type="search" aria-label={label} className={cx(control, "h-10 pl-9 text-sm")} />
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        "relative inline-flex h-7 w-12 shrink-0 items-center rounded-full p-0.5 transition-colors disabled:opacity-60",
        checked ? "bg-primary" : "bg-line-strong",
      )}
    >
      <span
        aria-hidden
        className={cx(
          "h-6 w-6 rounded-full bg-white shadow-card transition-transform duration-200",
          checked ? "translate-x-5" : "translate-x-0",
        )}
      />
    </button>
  );
}

/**
 * Mutually exclusive filter. Scrolls sideways on small screens rather than wrapping. The
 * highlight slides to the chosen option, so the change of view is visible.
 */
export function Segmented<V extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: Array<{ value: V; label: ReactNode; count?: number }>;
  value: V;
  onChange: (value: V) => void;
  label: string;
}) {
  const refs = useRef(new Map<string, HTMLButtonElement>());
  const [pill, setPill] = useState<{ left: number; width: number } | null>(null);
  useLayoutEffect(() => {
    const measure = () => {
      const el = refs.current.get(value);
      if (el) setPill({ left: el.offsetLeft, width: el.offsetWidth });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [value, options.length]);
  return (
    <div className="-mx-1 min-w-0 overflow-x-auto px-1 py-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      <div role="radiogroup" aria-label={label} className="relative inline-flex gap-1 rounded-lg border border-line bg-subtle p-1">
        {pill && (
          <span
            aria-hidden
            className="absolute inset-y-1 rounded-md bg-surface shadow-card ring-1 ring-line transition-[transform,width] duration-200 ease-out"
            style={{ left: 0, width: pill.width, transform: `translateX(${pill.left}px)` }}
          />
        )}
        {options.map((o) => {
          const active = o.value === value;
          return (
            <button
              key={o.value}
              ref={(el) => {
                if (el) refs.current.set(o.value, el);
              }}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onChange(o.value)}
              className={cx(
                "relative inline-flex min-h-8 items-center gap-1.5 whitespace-nowrap rounded-md px-3 text-[13px] font-semibold transition-colors",
                active ? "text-ink" : "text-ink-muted hover:text-ink",
                !pill && active && "bg-surface shadow-card ring-1 ring-line",
              )}
            >
              {o.label}
              {o.count !== undefined && (
                <span
                  className={cx(
                    "tabular rounded px-1 text-xs font-medium transition-colors",
                    active ? "bg-primary-soft text-primary-ink" : "text-ink-subtle",
                  )}
                >
                  {o.count.toLocaleString()}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Row of filters above a list. Stacks on small screens. */
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between", className)}>
      {children}
    </div>
  );
}

/* ---------------------------------------------------------------- timeline */

export function Timeline({ children }: { children: ReactNode }) {
  return <ol className="relative space-y-5 before:absolute before:inset-y-1 before:left-[11px] before:w-px before:bg-line">{children}</ol>;
}

export function TimelineItem({
  icon,
  tone = "neutral",
  title,
  meta,
  children,
}: {
  icon?: ReactNode;
  tone?: "neutral" | "brand" | "ok" | "warn" | "bad";
  title: ReactNode;
  meta?: ReactNode;
  children?: ReactNode;
}) {
  const dot = {
    neutral: "bg-surface text-ink-subtle ring-line-strong",
    brand: "bg-primary-soft text-primary-ink ring-primary-line",
    ok: "bg-ok-soft text-ok ring-ok-line",
    warn: "bg-warn-soft text-warn ring-warn-line",
    bad: "bg-bad-soft text-bad ring-bad-line",
  };
  return (
    <li className="relative flex gap-3.5">
      <span className={cx("relative z-[1] mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full ring-1", dot[tone])}>
        {icon ?? <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-sm text-ink">{title}</div>
        {meta && <div className="mt-0.5 text-[13px] text-ink-subtle">{meta}</div>}
        {children}
      </div>
    </li>
  );
}

/* --------------------------------------------------------------- channels */

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

export function ChannelIcon({ channel, className = "h-4 w-4" }: { channel: string | null | undefined; className?: string }) {
  const Icon =
    channel === "sms"
      ? Smartphone
      : channel === "email"
        ? Mail
        : channel === "phone"
          ? Phone
          : channel === "rep_visit"
            ? UserRound
            : MessageSquare;
  return <Icon className={className} aria-hidden />;
}

/* --------------------------------------------------------------- formatting */

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

export function num(value: number | null | undefined, digits = 0) {
  return value === null || value === undefined
    ? "—"
    : value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

/** A value from the API as a Date. Date-only values ("2026-10-05") are calendar days;
 *  timestamps are UTC instants (the API sends them without an offset) and are shown in the
 *  viewer's local time. */
export function toDate(value: string): Date {
  if (value.length === 10) return new Date(`${value}T00:00:00`);
  const hasZone = /(Z|[+-]\d\d:?\d\d)$/.test(value);
  return new Date(hasZone ? value : `${value.replace(" ", "T")}Z`);
}

export function fmtDate(value: string | null | undefined) {
  if (!value) return "—";
  const d = toDate(value);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** An email address that wraps only after the @ on narrow screens, never mid-word. */
export function EmailText({ email, className }: { email: string; className?: string }) {
  const at = email.indexOf("@");
  if (at < 0) return <span className={className}>{email}</span>;
  return (
    <span className={cx(className, "[overflow-wrap:anywhere]")}>
      {email.slice(0, at)}
      <wbr />@{email.slice(at + 1)}
    </span>
  );
}

export function fmtDateTime(value: string | null | undefined) {
  if (!value) return "—";
  return toDate(value).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Slide-over panel from the right. Escape or the backdrop closes it; focus moves into it.
 *  Rendered into <body>, so no page container (transforms, stacking) can place it under
 *  the sticky top bar. */
export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      previous?.focus?.();
    };
  }, [onClose]);
  return createPortal(
    <div style={{ zIndex: "var(--z-drawer)", position: "relative" }}>
      <div className="animate-fade fixed inset-0 bg-black/40" aria-hidden onClick={onClose} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className="animate-drawer-right fixed inset-y-0 right-0 flex w-full max-w-[34rem] flex-col bg-surface shadow-overlay outline-none"
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-line px-5 sm:px-6">
          <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
          <IconButton label="Close" onClick={onClose}>
            <X className="h-5 w-5" aria-hidden />
          </IconButton>
        </div>
        <div className="scroll-quiet flex-1 overflow-y-auto px-5 py-6 sm:px-6">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
