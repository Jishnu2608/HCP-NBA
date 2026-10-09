// Page composition: the bento grid used by dashboards and other information-dense screens.
// Adapted from the bento-grid idea (one grid, a few deliberate cell sizes, cards that fill
// their cell) to this product's calm visual language. No animation library is involved.
//
// Grid: 1 column on phones, 2 from 768px, 12 from 1280px. Cells take one of four sizes:
//   full   - 12 of 12   (always full width)
//   wide   -  8 of 12   (primary content; full width below 1280px)
//   half   -  6 of 12   (paired content; full width below 1280px, charts need the width)
//   narrow -  4 of 12   (supporting content; half width from 768px when `pairOnTablet`)
//   auto   - no span of its own (cards inside a BentoSplit column)
// Cards stretch to the tallest cell in their row, so neighbouring cards align; pair only
// cards of similar height in one row. A long or growing list never shares a row with short
// cards: it goes full width, or into a BentoSplit, where each column stacks its own cards
// and nothing stretches.
import { ArrowRight } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { gsap, reducedMotion, useEntrance, useGSAP, useSpotlightGrid } from "./motion";
import { cx } from "./ui";

type Span = "full" | "wide" | "half" | "narrow" | "auto";
const SPAN: Record<Span, string> = {
  full: "xl:col-span-12",
  wide: "xl:col-span-8",
  half: "xl:col-span-6",
  narrow: "xl:col-span-4",
  auto: "",
};

function spanClass(span: Span, pairOnTablet: boolean) {
  if (span === "auto") return "";
  return cx(SPAN[span], pairOnTablet && span !== "full" ? "md:col-span-1" : "md:col-span-2");
}

/**
 * A grid cell for content that brings its own card (for example the open-recommendation
 * card). The child is stretched to fill the cell, so it aligns with neighbouring cards.
 */
export function BentoCell({
  span = "half",
  pairOnTablet = false,
  children,
  className,
  id,
}: {
  span?: Span;
  pairOnTablet?: boolean;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <div id={id} className={cx("flex min-w-0 scroll-mt-32 flex-col [&>*]:flex-1", spanClass(span, pairOnTablet), className)}>
      {children}
    </div>
  );
}

/** The grid enters after the page heading and figures: first cells lift in, in order, once. */
export function BentoGrid({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEntrance(ref, { delay: 0.1 });
  useSpotlightGrid(ref);
  return (
    <div ref={ref} className={cx("grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-12", className)}>
      {children}
    </div>
  );
}

const SPLIT = {
  "8/4": ["xl:col-span-8", "xl:col-span-4"],
  "7/5": ["xl:col-span-7", "xl:col-span-5"],
  "6/6": ["xl:col-span-6", "xl:col-span-6"],
} as const;

/**
 * Two independent columns on the 12-column grid: primary content and supporting cards.
 * Each column stacks its own cards at their natural height, so a long card never stretches
 * its neighbour and the next card fills the space under a short one. Below 1280px the main
 * column comes first at full width; the supporting cards pair two-up from 768px (a lone or
 * last odd card takes the whole row). Cards inside use `span="auto"`.
 */
export function BentoSplit({
  main,
  aside,
  ratio = "8/4",
  className,
}: {
  main: ReactNode;
  aside: ReactNode;
  ratio?: keyof typeof SPLIT;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEntrance(ref, { delay: 0.1, selector: ":scope > div > *" });
  useSpotlightGrid(ref);
  const [left, right] = SPLIT[ratio];
  // Nothing to put beside the main column: it takes the full width rather than leaving one empty.
  if (!aside)
    return (
      <div ref={ref} className={cx("flex min-w-0 flex-col gap-4", className)}>
        {main}
      </div>
    );
  return (
    <div ref={ref} className={cx("grid grid-cols-1 items-start gap-4 xl:grid-cols-12", className)}>
      <div className={cx("flex min-w-0 flex-col gap-4", left)}>{main}</div>
      <div
        className={cx(
          "grid min-w-0 content-start gap-4 md:grid-cols-2 xl:grid-cols-1",
          "md:[&>*:last-child:nth-child(odd)]:col-span-2 xl:[&>*:last-child:nth-child(odd)]:col-span-1",
          right,
        )}
      >
        {aside}
      </div>
    </div>
  );
}

/**
 * A titled panel that fills its grid cell. Optional icon tile in the header, optional
 * "View all" link, optional footer note pinned to the bottom so notes in a row line up.
 */
export function BentoCard({
  span = "half",
  pairOnTablet = false,
  title,
  description,
  icon,
  link,
  action,
  note,
  children,
  className,
  bodyClassName,
  id,
}: {
  /** Anchor for in-page navigation (SectionNav). */
  id?: string;
  span?: Span;
  /** Narrow and half cells sit two-up between 768 and 1279px instead of full width. */
  pairOnTablet?: boolean;
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
      id={id}
      aria-labelledby={headingId}
      data-spot
      className={cx(
        "spotlight flex min-w-0 scroll-mt-32 flex-col rounded-xl border border-line bg-surface shadow-card",
        spanClass(span, pairOnTablet),
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
      {/* A column, so a chart can grow into a card stretched by its neighbour. */}
      <div className={cx("flex flex-1 flex-col px-5 pb-5 pt-4 sm:px-6", bodyClassName)}>{children}</div>
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
        <div className={cx("animate-grow h-full w-full origin-left rounded-full transition-transform duration-300", fill)} style={{ transform: `scaleX(${v})` }} />
      </div>
      {sub && <div className="mt-1 text-xs text-ink-subtle">{sub}</div>}
    </li>
  );
}

/**
 * A compact navigator for a long record page (Patient 360): one link per section, sticky
 * under the top bar. The marker under the active link glides as the reader scrolls, so
 * it is always clear where on the page they are; selecting a link scrolls there (instantly
 * under reduced motion). Sections that are not on the page are left out.
 */
export function SectionNav({ items, label }: { items: Array<{ id: string; label: string }>; label: string }) {
  const [present, setPresent] = useState<Array<{ id: string; label: string }>>([]);
  const [active, setActive] = useState<string | null>(null);
  const bar = useRef<HTMLDivElement>(null);
  const marker = useRef<HTMLSpanElement>(null);
  const links = useRef(new Map<string, HTMLAnchorElement>());
  const signature = items.map((i) => i.id).join("|");

  useEffect(() => {
    // Sections can arrive after the page (the care panel loads separately), so the list of
    // sections is refreshed when the page content changes, at most once per frame.
    const pick = () => {
      const line = window.innerHeight * 0.3;
      const targets = items.map((i) => document.getElementById(i.id)).filter(Boolean) as HTMLElement[];
      let current = targets[0]?.id ?? null;
      for (const t of targets) if (t.getBoundingClientRect().top - line <= 0) current = t.id;
      setActive(current);
    };
    const io = new IntersectionObserver(pick, { rootMargin: "-20% 0px -60% 0px", threshold: [0, 1] });
    let ids = "";
    let frame = 0;
    const refresh = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const found = items.filter((i) => document.getElementById(i.id));
        const next = found.map((i) => i.id).join("|");
        if (next === ids) return;
        ids = next;
        setPresent(found);
        io.disconnect();
        found.forEach((i) => io.observe(document.getElementById(i.id)!));
        pick();
      });
    };
    refresh();
    const mo = new MutationObserver(refresh);
    mo.observe(document.querySelector("main") ?? document.body, { childList: true, subtree: true });
    return () => {
      cancelAnimationFrame(frame);
      mo.disconnect();
      io.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  useGSAP(
    () => {
      const el = active ? links.current.get(active) : null;
      if (!el || !marker.current) return;
      // Transform only: the marker is 100px wide and scaled to the link's width.
      const props = { x: el.offsetLeft, scaleX: el.offsetWidth / 100, opacity: 1 };
      if (reducedMotion()) gsap.set(marker.current, props);
      else gsap.to(marker.current, { ...props, duration: 0.28, ease: "power3.out", overwrite: true });
      // Keep the active link in view on narrow screens (horizontal scroll of the bar only).
      const box = bar.current;
      if (box && (el.offsetLeft < box.scrollLeft || el.offsetLeft + el.offsetWidth > box.scrollLeft + box.clientWidth))
        box.scrollTo({ left: el.offsetLeft - 16, behavior: reducedMotion() ? "auto" : "smooth" });
    },
    { dependencies: [active, present.length], scope: bar },
  );

  if (present.length < 2) return null;
  return (
    <nav aria-label={label} className="sticky top-16 mb-6 -mx-4 border-b border-line bg-canvas/95 px-4 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8" style={{ zIndex: 5 }}>
      <div ref={bar} className="relative flex gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {present.map((i) => (
          <a
            key={i.id}
            ref={(el) => {
              if (el) links.current.set(i.id, el);
            }}
            href={`#${i.id}`}
            aria-current={active === i.id ? "location" : undefined}
            onClick={(e) => {
              e.preventDefault();
              document.getElementById(i.id)?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
              setActive(i.id);
            }}
            className={cx(
              "inline-flex min-h-11 shrink-0 items-center whitespace-nowrap px-3 text-[13px] font-semibold transition-colors",
              active === i.id ? "text-primary-ink" : "text-ink-muted hover:text-ink",
            )}
          >
            {i.label}
          </a>
        ))}
        <span ref={marker} aria-hidden className="pointer-events-none absolute bottom-0 left-0 h-0.5 w-[100px] origin-left rounded-full bg-primary opacity-0" />
      </div>
    </nav>
  );
}
