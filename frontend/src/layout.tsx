// Page composition: the bento grid used by dashboards and other information-dense screens.
// Adapted from the bento-grid idea (one grid, a few deliberate cell sizes, cards that fill
// their cell) to this product's calm visual language. No animation library is involved.
//
// Grid: 1 column on phones, 2 from 768px, 12 from 1280px. Cells take one of four sizes:
//   full   - 12 of 12   (always full width)
//   wide   -  8 of 12   (primary content; full width below 1280px)
//   half   -  6 of 12   (paired content; full width below 1280px, charts need the width)
//   narrow -  4 of 12   (supporting content; half width from 768px when `pairOnTablet`)
// A cell can be two rows tall (`rows={2}`, from 1280px) so a long list sits beside two
// stacked supporting cards. Cards stretch to the tallest cell in their row, so neighbouring
// cards always align.
import { ArrowRight } from "lucide-react";
import { useId } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cx } from "./ui";

type Span = "full" | "wide" | "half" | "narrow";
const SPAN: Record<Span, string> = {
  full: "xl:col-span-12",
  wide: "xl:col-span-8",
  half: "xl:col-span-6",
  narrow: "xl:col-span-4",
};

const ROWS = { 1: "", 2: "xl:row-span-2" } as const;

function spanClass(span: Span, pairOnTablet: boolean, rows: 1 | 2) {
  return cx(SPAN[span], pairOnTablet && span !== "full" ? "md:col-span-1" : "md:col-span-2", ROWS[rows]);
}

/**
 * A grid cell for content that brings its own card (for example the open-recommendation
 * card). The child is stretched to fill the cell, so it aligns with neighbouring cards.
 */
export function BentoCell({
  span = "half",
  pairOnTablet = false,
  rows = 1,
  children,
  className,
}: {
  span?: Span;
  pairOnTablet?: boolean;
  rows?: 1 | 2;
  children: ReactNode;
  className?: string;
}) {
  return <div className={cx("flex min-w-0 flex-col [&>*]:flex-1", spanClass(span, pairOnTablet, rows), className)}>{children}</div>;
}

export function BentoGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("stagger grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-12", className)}>{children}</div>;
}

/**
 * A titled panel that fills its grid cell. Optional icon tile in the header, optional
 * "View all" link, optional footer note pinned to the bottom so notes in a row line up.
 */
export function BentoCard({
  span = "half",
  pairOnTablet = false,
  rows = 1,
  title,
  description,
  icon,
  link,
  action,
  note,
  children,
  className,
  bodyClassName,
}: {
  span?: Span;
  /** Narrow and half cells sit two-up between 768 and 1279px instead of full width. */
  pairOnTablet?: boolean;
  /** Two rows tall from 1280px. */
  rows?: 1 | 2;
  title: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  link?: { to: string; label: string };
  action?: ReactNode;
  note?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  const headingId = useId();
  return (
    <section
      aria-labelledby={headingId}
      className={cx(
        "flex min-w-0 flex-col rounded-xl border border-line bg-surface shadow-card",
        spanClass(span, pairOnTablet, rows),
        className,
      )}
    >
      <header className="flex items-start gap-3 px-5 pt-5 sm:px-6">
        {icon && (
          <span
            aria-hidden
            className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-subtle text-ink-muted [&>svg]:h-4 [&>svg]:w-4"
          >
            {icon}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <h3 id={headingId} className="text-[15px] font-semibold leading-6 text-ink">
            {title}
          </h3>
          {description && <p className="mt-0.5 text-[13px] leading-5 text-ink-subtle">{description}</p>}
        </div>
        {action}
        {link && (
          <Link
            to={link.to}
            className="group inline-flex min-h-8 shrink-0 items-center gap-1 rounded-md px-2 text-[13px] font-semibold text-primary-ink hover:bg-primary-soft"
          >
            {link.label}
            <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" aria-hidden />
          </Link>
        )}
      </header>
      <div className={cx("flex-1 px-5 pb-5 pt-4 sm:px-6", bodyClassName)}>{children}</div>
      {note && <p className="border-t border-line px-5 py-3 text-[13px] leading-5 text-ink-subtle sm:px-6">{note}</p>}
    </section>
  );
}

/** Heading for a group of cards on a long page. Gives the page a visible reading order. */
export function SectionHeader({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("mb-4 mt-10 flex flex-wrap items-end justify-between gap-x-6 gap-y-2 first:mt-0", className)}>
      <div className="min-w-0">
        <h2 className="text-lg font-semibold tracking-[-0.01em] text-ink">{title}</h2>
        {description && <p className="mt-0.5 max-w-3xl text-sm text-ink-subtle">{description}</p>}
      </div>
      {action}
    </div>
  );
}

/** One plain-language finding with its supporting figure, for insight lists. */
export function InsightRow({
  icon,
  tone = "neutral",
  children,
  figure,
}: {
  icon: ReactNode;
  tone?: "neutral" | "ok" | "warn" | "bad" | "brand";
  children: ReactNode;
  figure?: ReactNode;
}) {
  const tile = {
    neutral: "bg-subtle text-ink-muted",
    ok: "bg-ok-soft text-ok",
    warn: "bg-warn-soft text-warn",
    bad: "bg-bad-soft text-bad",
    brand: "bg-primary-soft text-primary-ink",
  }[tone];
  return (
    <li className="flex gap-3 py-3.5 first:pt-0 last:pb-0">
      <span aria-hidden className={cx("grid h-8 w-8 shrink-0 place-items-center rounded-lg [&>svg]:h-4 [&>svg]:w-4", tile)}>
        {icon}
      </span>
      <div className="min-w-0 flex-1 text-sm leading-6 text-ink">{children}</div>
      {figure && <div className="tabular shrink-0 text-sm font-semibold text-ink">{figure}</div>}
    </li>
  );
}

/** Labelled proportion row (label, figure, bar), used for ranked lists inside cards. */
export function BarRow({
  label,
  figure,
  value,
  tone = "brand",
  sub,
}: {
  label: ReactNode;
  figure: ReactNode;
  value: number;
  tone?: "brand" | "ok" | "warn" | "bad" | "neutral";
  sub?: ReactNode;
}) {
  const fill = { brand: "bg-primary", ok: "bg-ok-fill", warn: "bg-warn-fill", bad: "bg-bad-fill", neutral: "bg-line-strong" }[tone];
  const v = Math.max(0, Math.min(1, value));
  return (
    <li>
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="min-w-0 text-ink">{label}</span>
        <span className="tabular shrink-0 font-semibold text-ink">{figure}</span>
      </div>
      <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-sunken">
        <div className={cx("animate-grow h-full origin-left rounded-full", fill)} style={{ width: `${v * 100}%` }} />
      </div>
      {sub && <div className="mt-1 text-xs text-ink-subtle">{sub}</div>}
    </li>
  );
}
