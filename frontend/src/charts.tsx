// Chart primitives for the role pages. They draw what `GET /api/insights/*` returns and hold
// no business logic: every figure, threshold and label is decided on the server
// (backend/app/insights), so a number means the same thing on every page.
//
// Rules every primitive follows:
// - colours come from the theme tokens (index.css), so light and dark are both designed;
// - status is never colour alone: legends carry an icon and a word;
// - every mark has a short tooltip on hover and on keyboard focus (marks are focusable, or
//   their table view is, when there are too many to tab through);
// - loading, error, empty and "not enough data yet" states are explicit (ChartPanel);
// - important charts offer a table view of the same numbers.
//
// Plain SVG and HTML, no chart library: these load with the role pages, and the Recharts
// bundle stays with the analytics dashboard.
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  BarChart3,
  Check,
  Circle,
  Clock,
  Info,
  Minus,
  Repeat,
  Table2,
} from "lucide-react";
import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { RefObject } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { api } from "./api";
import { preferences } from "./session";
import type { Json } from "./api";
import { useAuth } from "./auth";
import { BentoCard } from "./layout";
import { DUR, changedSinceSeen, gsap, isTouch, reducedMotion, useGSAP } from "./motion";
import { EmptyState, ErrorState, LoadingRows, cx, fmtDate, num, toDate } from "./ui";

/* ------------------------------------------------------------------ data */

export type InsightArea = "patient" | "hcp" | "care" | "content" | "rep" | "operations";

/**
 * Figures for one working area. Refreshed every minute and when the window regains focus
 * (other people's actions), and invalidated after any of the user's own changes (the
 * mutation cache in main.tsx), so a chart never lags the records it shows.
 */
export function useInsights(area: InsightArea, enabled = true) {
  return useQuery({
    queryKey: ["insights", area],
    queryFn: () => api<Json>(`/insights/${area}`),
    refetchInterval: 60_000,
    enabled,
  });
}

/* ------------------------------------------------------------------ tones */

export type ChartTone = "ok" | "warn" | "bad" | "info" | "neutral" | "brand";

const FILL: Record<ChartTone, string> = {
  ok: "bg-ok-fill",
  warn: "bg-warn-fill",
  bad: "bg-bad-fill",
  info: "bg-info-fill",
  neutral: "bg-line-strong",
  brand: "bg-primary",
};
export const SVG_FILL: Record<ChartTone, string> = {
  ok: "var(--ok-fill)",
  warn: "var(--warn-fill)",
  bad: "var(--bad-fill)",
  info: "var(--info-fill)",
  neutral: "var(--line-strong)",
  brand: "var(--primary)",
};
const ICON: Record<ChartTone, ReactNode> = {
  ok: <Check aria-hidden />,
  warn: <Clock aria-hidden />,
  bad: <AlertTriangle aria-hidden />,
  info: <Info aria-hidden />,
  neutral: <Circle aria-hidden />,
  brand: <Circle aria-hidden />,
};
const ICON_TEXT: Record<ChartTone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  info: "text-info",
  neutral: "text-ink-subtle",
  brand: "text-primary-ink",
};

/** A swatch with its status icon, for legends. */
export function ToneKey({ tone }: { tone: ChartTone }) {
  return (
    <span className={cx("inline-flex items-center gap-1 [&>svg]:h-3.5 [&>svg]:w-3.5", ICON_TEXT[tone])}>
      <span className={cx("h-2.5 w-2.5 rounded-sm", FILL[tone])} aria-hidden />
      {ICON[tone]}
    </span>
  );
}

/* ------------------------------------------------------------------ tooltip */

export type Tip = { title: string; lines?: Array<string | null | undefined | false> };

const tipText = (t: Tip) => [t.title, ...(t.lines ?? []).filter(Boolean)].join(". ");

type TipState = { tip: Tip; x: number; y: number } | null;
const TipContext = createContext<(tip: Tip | null, el?: Element) => void>(() => {});

/**
 * A chart draws itself in once, the first time it is on screen: bars grow from their
 * baseline, lines draw, points and cells fade in. Values and labels are final from the
 * start, so nothing ever shows a false number; later data changes use the marks' own
 * transitions and never replay this. Marks opt in with `data-draw`:
 *   "x" / "y"  bars that grow along that axis (at most 12 move; the rest are already there)
 *   "line"     an SVG path that draws
 *   "fade"     a group (for example all scatter points) that fades in as one
 *   "cells"    a row of small cells revealed left to right in one short sweep
 * Charts already in view at mount animate before the first paint; others wait for an
 * IntersectionObserver (no ScrollTrigger on app pages).
 */
function useDrawIn(scope: RefObject<HTMLDivElement | null>) {
  useGSAP(
    () => {
      const root = scope.current;
      if (!root || reducedMotion()) return;
      const play = () => {
        const max = isTouch() ? 6 : 12;
        const tl = gsap.timeline({ defaults: { duration: 0.42, ease: "power2.out" } });
        const xs = Array.from(root.querySelectorAll<HTMLElement>('[data-draw="x"]')).slice(0, max);
        const ys = Array.from(root.querySelectorAll<HTMLElement>('[data-draw="y"]')).slice(0, max);
        // Bars keep their own inline scale as the end state; their CSS transition (used for
        // later value changes) is paused during the draw so the two never fight.
        const bars = [...xs, ...ys];
        if (bars.length) gsap.set(bars, { transition: "none" });
        if (xs.length) tl.from(xs, { scaleX: 0, transformOrigin: "0% 50%", stagger: 0.03 }, 0);
        if (ys.length) tl.from(ys, { scaleY: 0, transformOrigin: "50% 100%", stagger: 0.03 }, 0);
        if (bars.length) tl.eventCallback("onComplete", () => gsap.set(bars, { clearProps: "transition" }));
        root.querySelectorAll<SVGPathElement>('[data-draw="line"]').forEach((path) => {
          const length = path.getTotalLength();
          tl.fromTo(path, { strokeDasharray: length, strokeDashoffset: length }, { strokeDashoffset: 0, duration: 0.5, clearProps: "strokeDasharray,strokeDashoffset" }, 0);
        });
        const fades = root.querySelectorAll('[data-draw="fade"]');
        if (fades.length) tl.from(fades, { opacity: 0, duration: 0.3, clearProps: "opacity" }, 0.1);
        root.querySelectorAll<HTMLElement>('[data-draw="cells"]').forEach((row) => {
          tl.from(row.children, { opacity: 0, duration: 0.2, stagger: { amount: 0.4 }, clearProps: "opacity" }, 0);
        });
      };
      const r = root.getBoundingClientRect();
      if (r.top < window.innerHeight && r.bottom > 0) return play();
      const io = new IntersectionObserver(
        (entries) => {
          if (entries.some((e) => e.isIntersecting)) {
            io.disconnect();
            play();
          }
        },
        { threshold: 0.2 },
      );
      io.observe(root);
      return () => io.disconnect();
    },
    { scope },
  );
}

/** Positions one small tooltip for every mark inside it, and draws its chart in once. */
export function ChartFrame({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useDrawIn(ref);
  const [state, setState] = useState<TipState>(null);
  const show = useCallback((tip: Tip | null, el?: Element) => {
    if (!tip || !el || !ref.current) return setState(null);
    const frame = ref.current.getBoundingClientRect();
    const mark = el.getBoundingClientRect();
    setState({ tip, x: mark.left - frame.left + mark.width / 2, y: mark.top - frame.top });
  }, []);
  const width = ref.current?.clientWidth ?? 0;
  return (
    <TipContext.Provider value={show}>
      <div ref={ref} className={cx("relative", className)} onMouseLeave={() => setState(null)}>
        {children}
        {state && (
          <div
            role="tooltip"
            className="pointer-events-none absolute z-20 max-w-[16rem] -translate-y-full rounded-lg border border-line-strong bg-surface px-2.5 py-1.5 text-xs leading-5 text-ink shadow-md"
            style={{
              left: Math.max(4, Math.min(state.x - 64, Math.max(4, width - 200))),
              top: state.y - 6,
            }}
          >
            <p className="font-semibold">{state.tip.title}</p>
            {(state.tip.lines ?? []).filter(Boolean).map((l, i) => (
              <p key={i} className="text-ink-muted">
                {l}
              </p>
            ))}
          </div>
        )}
      </div>
    </TipContext.Provider>
  );
}

/**
 * Props that make an element a chart mark: tooltip on hover and focus, an accessible
 * label, and an optional action on click / Enter. `focusable: false` for dense charts whose
 * table view carries the keyboard path.
 */
export function useMark() {
  const show = useContext(TipContext);
  return (tip: Tip, onActivate?: () => void, focusable = true) => ({
    tabIndex: focusable ? 0 : -1,
    role: onActivate ? "button" : "img",
    "aria-label": tipText(tip),
    onMouseEnter: (e: { currentTarget: Element }) => show(tip, e.currentTarget),
    onFocus: (e: { currentTarget: Element }) => show(tip, e.currentTarget),
    onBlur: () => show(null),
    onClick: onActivate,
    onKeyDown: onActivate
      ? (e: KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onActivate();
          }
        }
      : undefined,
    "data-mark": "",
  });
}

/* ------------------------------------------------------------------ panel */

/**
 * A chart's card: the question it answers, its states, and an optional table view of the
 * same numbers. `empty` replaces the chart with an honest message when there is nothing (or
 * not enough) to draw.
 */
export function ChartPanel({
  title,
  question,
  icon,
  span = "full",
  pairOnTablet,
  query,
  empty,
  table,
  note,
  link,
  children,
}: {
  title: string;
  question: ReactNode;
  icon?: ReactNode;
  span?: "full" | "wide" | "half" | "narrow" | "auto";
  pairOnTablet?: boolean;
  query?: { isLoading: boolean; error: unknown; refetch: () => unknown };
  empty?: ReactNode | false;
  table?: ReactNode;
  note?: ReactNode;
  link?: { to: string; label: string };
  children?: ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  const ready = !query?.isLoading && !query?.error && !empty;
  const toggle =
    table && ready ? (
      <button
        type="button"
        onClick={() => setAsTable((v) => !v)}
        aria-pressed={asTable}
        className="inline-flex min-h-8 shrink-0 items-center gap-1.5 rounded-md px-2 text-[13px] font-semibold text-primary-ink hover:bg-primary-soft focus-visible:outline-2 focus-visible:outline-focus"
      >
        {asTable ? <BarChart3 className="h-3.5 w-3.5" aria-hidden /> : <Table2 className="h-3.5 w-3.5" aria-hidden />}
        {asTable ? "Chart" : "Table"}
      </button>
    ) : undefined;
  return (
    // A chart beside a taller neighbour sits in the middle of its stretched card, so the
    // spare height is shared above and below instead of collecting under the chart.
    <BentoCard
      span={span}
      pairOnTablet={pairOnTablet}
      title={title}
      description={question}
      icon={icon}
      action={toggle}
      note={note}
      link={link}
      bodyClassName="flex flex-col justify-center"
    >
      {query?.isLoading ? (
        <LoadingRows rows={3} label={`Loading ${title.toLowerCase()}`} />
      ) : query?.error ? (
        <ErrorState error={query.error} retry={() => void query.refetch()} title="Unable to load this chart" />
      ) : empty ? (
        <EmptyState compact icon={<BarChart3 className="h-5 w-5" />} title={empty} />
      ) : asTable ? (
        table
      ) : (
        <ChartFrame>{children}</ChartFrame>
      )}
    </BentoCard>
  );
}

/** A small table for a chart's numbers. */
export function ChartTable({ caption, head, rows }: { caption: string; head: string[]; rows: Array<Array<ReactNode>> }) {
  return (
    <div className="scroll-quiet -mx-1 max-h-80 overflow-auto px-1">
      <table className="w-full border-collapse text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 bg-surface">
          <tr className="border-b border-line">
            {head.map((h, i) => (
              <th key={i} scope="col" className={cx("whitespace-nowrap px-2 py-2 text-[13px] font-medium text-ink-subtle first:pl-0", i > 0 && "text-right")}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j} className={cx("px-2 py-2 first:pl-0", j > 0 && "tabular text-right")}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ stacked bar */

export type Segment = { key: string; label: string; value: number; tone: ChartTone; hint?: string };

/** Parts of one whole, with a legend that names and counts every part. */
export function StackedBar({ segments, unit, onSelect }: { segments: Segment[]; unit: string; onSelect?: (s: Segment) => void }) {
  const mark = useMark();
  const total = segments.reduce((n, s) => n + s.value, 0);
  const shown = segments.filter((s) => s.value > 0);
  return (
    <div>
      <div className="flex h-3.5 gap-[2px] overflow-hidden rounded-full bg-sunken">
        {shown.map((s) => (
          <div
            key={s.key + s.label}
            data-draw="x"
            className={cx("h-full transition-[width] duration-300 first:rounded-l-full last:rounded-r-full outline-offset-2 focus-visible:outline-2 focus-visible:outline-focus", FILL[s.tone])}
            style={{ width: `${(s.value / Math.max(total, 1)) * 100}%` }}
            {...mark(
              { title: s.label, lines: [`${num(s.value)} ${unit}${s.value === 1 ? "" : "s"}`, total ? `${Math.round((s.value / total) * 100)}% of ${num(total)}` : null, s.hint] },
              onSelect ? () => onSelect(s) : undefined,
            )}
          />
        ))}
      </div>
      <ul className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-2">
        {segments.map((s) => {
          const body = (
            <>
              <ToneKey tone={s.tone} />
              <span className="min-w-0 flex-1 truncate text-left text-ink-muted">{s.label}</span>
              <span className="tabular font-semibold text-ink">{num(s.value)}</span>
            </>
          );
          return (
            <li key={s.key + s.label}>
              {onSelect && s.value > 0 ? (
                <button type="button" onClick={() => onSelect(s)} className="flex w-full items-center gap-2 rounded-md px-1 py-0.5 hover:bg-subtle focus-visible:outline-2 focus-visible:outline-focus">
                  {body}
                </button>
              ) : (
                <div className="flex items-center gap-2 px-1 py-0.5">{body}</div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ bar list */

export type BarItem = {
  key: string;
  label: ReactNode;
  value: number;
  figure?: ReactNode;
  sub?: ReactNode;
  tone?: ChartTone;
  tip: Tip;
  onOpen?: () => void;
};

/** Ranked horizontal bars with direct labels. Long labels wrap on phones. */
export function BarList({ items, max }: { items: BarItem[]; max?: number }) {
  const mark = useMark();
  const top = Math.max(1, max ?? Math.max(0, ...items.map((i) => i.value)));
  return (
    <ul className="space-y-3">
      {items.map((i) => (
        <li key={i.key}>
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0 text-ink">{i.label}</span>
            <span className="tabular shrink-0 font-semibold text-ink">{i.figure ?? num(i.value)}</span>
          </div>
          <div
            className="mt-1.5 h-2 rounded-full bg-sunken outline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
            {...mark(i.tip, i.onOpen)}
          >
            <div
              data-draw="x"
              className={cx("h-full w-full origin-left rounded-full transition-transform duration-300", FILL[i.tone ?? "brand"])}
              style={{ transform: `scaleX(${Math.max(i.value > 0 ? 0.02 : 0, Math.min(1, i.value / top))})` }}
            />
          </div>
          {i.sub && <div className="mt-1 text-xs text-ink-subtle">{i.sub}</div>}
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------------ columns */

export type Column = { key: string; label: string; value: number; tone?: ChartTone; tip: Tip };

/** Vertical bars over categories (a distribution). Counts sit above each bar. */
export function Columns({ items, height = 120 }: { items: Column[]; height?: number }) {
  const mark = useMark();
  const top = Math.max(1, ...items.map((i) => i.value));
  return (
    <div>
      <div className="flex items-end gap-2" style={{ height }}>
        {items.map((i) => (
          <div key={i.key} className="flex h-full min-w-0 flex-1 flex-col justify-end">
            <span className="tabular mb-1 text-center text-xs font-semibold text-ink">{num(i.value)}</span>
            {/* The bar's track is full height; the coloured bar scales inside it. */}
            <div
              className="relative outline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
              style={{ height: height - 22 }}
              {...mark(i.tip)}
            >
              <div
                data-draw="y"
                className={cx("absolute inset-x-0 bottom-0 h-full origin-bottom rounded-t-[4px] transition-transform duration-300", FILL[i.tone ?? "brand"])}
                style={{ transform: `scaleY(${Math.max(i.value > 0 ? 3 : 1, (i.value / top) * (height - 22)) / (height - 22)})`, opacity: i.value ? 1 : 0.35 }}
              />
            </div>
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex gap-2 border-t border-line pt-1.5">
        {items.map((i) => (
          <span key={i.key} className="min-w-0 flex-1 text-center text-[11px] leading-4 text-ink-subtle">
            {i.label}
          </span>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ day strip */

export type DayCell = { date: string; state: "covered" | "gap" | "before"; tip: Tip };

const DAY_FILL = { covered: "bg-ok-fill", gap: "bg-bad-soft border border-bad-line", before: "bg-sunken" };

/** One cell per day: supply on hand, no supply, or not started. */
export function DayStrip({ days, label }: { days: DayCell[]; label: string }) {
  const mark = useMark();
  const row = useRef<HTMLDivElement>(null);
  const before = useRef<Map<string, string> | null>(null);
  const signature = days.map((d) => `${d.date}:${d.state}`).join("|");
  // Refill ripple: when real data changes a day's state during this visit (a refill recorded,
  // a supply ending), those days settle in from left to right. Nothing moves on first render.
  useGSAP(
    () => {
      const now = new Map(days.map((d) => [d.date, d.state as string]));
      const prev = before.current;
      before.current = now;
      if (!prev || !row.current || reducedMotion()) return;
      const changed = Array.from(row.current.querySelectorAll<HTMLElement>("[data-day]")).filter(
        (el) => prev.has(el.dataset.day!) && prev.get(el.dataset.day!) !== now.get(el.dataset.day!),
      );
      if (changed.length)
        gsap.fromTo(changed, { scaleY: 0.35, opacity: 0.4 }, { scaleY: 1, opacity: 1, duration: DUR.standard, stagger: { amount: 0.35 }, transformOrigin: "50% 100%", clearProps: "transform,opacity" });
    },
    { dependencies: [signature] },
  );
  return (
    <div>
      <div ref={row} data-draw="cells" className="grid gap-[2px]" style={{ gridTemplateColumns: `repeat(${days.length}, minmax(0, 1fr))` }} aria-label={label} role="group">
        {days.map((d) => (
          <div
            key={d.date}
            data-day={d.date}
            className={cx("h-6 rounded-[3px] outline-offset-1 focus-visible:outline-2 focus-visible:outline-focus", DAY_FILL[d.state])}
            {...mark(d.tip, undefined, false)}
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-ink-subtle">
        <span>{fmtDate(days[0]?.date).replace(/, \d{4}$/, "")}</span>
        <span>Today</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ sparkline */

/** A small trend line with every point labelled on hover and focus. */
export function Sparkline({
  points,
  label,
  height = 56,
  domain,
}: {
  points: Array<{ key: string; value: number; tip: Tip }>;
  label: string;
  height?: number;
  domain?: [number, number];
}) {
  const mark = useMark();
  const W = 240;
  const pad = 6;
  const lo = domain ? domain[0] : Math.min(...points.map((p) => p.value));
  const hi = domain ? domain[1] : Math.max(...points.map((p) => p.value));
  const span = hi - lo || 1;
  const x = (i: number) => pad + (i * (W - pad * 2)) / Math.max(1, points.length - 1);
  const y = (v: number) => pad + (1 - (v - lo) / span) * (height - pad * 2);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.value).toFixed(1)}`).join("");
  return (
    <svg viewBox={`0 0 ${W} ${height}`} className="h-auto w-full overflow-visible" role="group" aria-label={label}>
      <line x1={pad} x2={W - pad} y1={height - pad} y2={height - pad} stroke="var(--chart-grid)" />
      <path data-draw="line" d={path} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <g data-draw="fade">
      {points.map((p, i) => (
        <circle
          key={p.key}
          cx={x(i)}
          cy={y(p.value)}
          r={i === points.length - 1 ? 4 : 3}
          data-last={i === points.length - 1 ? "" : undefined}
          fill="var(--series-1)"
          stroke="var(--surface)"
          strokeWidth={2}
          className="focus-visible:outline-none"
          {...mark(p.tip)}
        />
      ))}
      </g>
    </svg>
  );
}

/* ------------------------------------------------------------------ scatter */

export type ScatterPoint = { key: string; x: number; y: number; tone: ChartTone; tip: Tip; onOpen?: () => void };

/**
 * Two measures per record, one point each. Points are too many to tab through, so they
 * are hover and click only: the panel's table view is the keyboard path (ChartPanel).
 */
export function Scatter({
  points,
  xLabel,
  yLabel,
  xMax,
  yMax,
  xGuides = [],
  yGuides = [],
  label,
}: {
  points: ScatterPoint[];
  xLabel: string;
  yLabel: string;
  xMax: number;
  yMax: number;
  xGuides?: Array<{ value: number; label: string }>;
  yGuides?: Array<{ value: number; label: string }>;
  label: string;
}) {
  const mark = useMark();
  const W = 640;
  const H = 260;
  const L = 40;
  const R = 12;
  const T = 10;
  const B = 34;
  const x = (v: number) => L + (Math.min(v, xMax) / xMax) * (W - L - R);
  const y = (v: number) => T + (1 - Math.min(v, yMax) / yMax) * (H - T - B);
  const ticks = (max: number) => [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * max));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="group" aria-label={label}>
      {ticks(yMax).map((t) => (
        <g key={`y${t}`}>
          <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--chart-grid)" />
          <text x={L - 6} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--chart-text)">
            {t}
          </text>
        </g>
      ))}
      {ticks(xMax).map((t) => (
        <text key={`x${t}`} x={x(t)} y={H - B + 16} textAnchor="middle" fontSize={11} fill="var(--chart-text)">
          {t}
          {t === xMax ? "+" : ""}
        </text>
      ))}
      {yGuides.map((g) => (
        <g key={g.label}>
          <line x1={L} x2={W - R} y1={y(g.value)} y2={y(g.value)} stroke="var(--ink-subtle)" strokeDasharray="4 4" />
          <text x={W - R} y={y(g.value) - 4} textAnchor="end" fontSize={11} fill="var(--ink-muted)">
            {g.label}
          </text>
        </g>
      ))}
      {xGuides.map((g) => (
        <g key={g.label}>
          <line x1={x(g.value)} x2={x(g.value)} y1={T} y2={H - B} stroke="var(--ink-subtle)" strokeDasharray="4 4" />
          <text x={x(g.value) + 4} y={T + 10} fontSize={11} fill="var(--ink-muted)">
            {g.label}
          </text>
        </g>
      ))}
      <text x={(L + W - R) / 2} y={H - 4} textAnchor="middle" fontSize={11} fill="var(--chart-text)">
        {xLabel}
      </text>
      <text
        x={12}
        y={(T + H - B) / 2}
        textAnchor="middle"
        fontSize={11}
        fill="var(--chart-text)"
        transform={`rotate(-90 12 ${(T + H - B) / 2})`}
      >
        {yLabel}
      </text>
      <g data-draw="fade">
      {points.map((p) => (
        <circle
          key={p.key}
          cx={x(p.x)}
          cy={y(p.y)}
          r={5}
          fill={SVG_FILL[p.tone]}
          fillOpacity={0.85}
          stroke="var(--surface)"
          strokeWidth={1.5}
          {...mark(p.tip, p.onOpen, false)}
        />
      ))}
      </g>
    </svg>
  );
}

/* ------------------------------------------------------------------ calendar */

export type CalendarDay = { date: string; parts: Array<{ label: string; value: number; tone: ChartTone }> };

/** The next days in a row, with what falls on each (dots per item, counts in the tooltip). */
export function CalendarStrip({ days, label }: { days: CalendarDay[]; label: string }) {
  const mark = useMark();
  return (
    <div className="scroll-quiet -mx-1 overflow-x-auto px-1 pb-1">
      <ol className="grid min-w-[560px] gap-1.5" style={{ gridTemplateColumns: `repeat(${days.length}, minmax(0, 1fr))` }} aria-label={label}>
        {days.map((d, i) => {
          const total = d.parts.reduce((n, p) => n + p.value, 0);
          const day = toDate(d.date);
          return (
            <li
              key={d.date}
              className={cx(
                "flex min-h-20 flex-col rounded-lg border px-1.5 py-1.5 outline-offset-2 focus-visible:outline-2 focus-visible:outline-focus",
                i === 0 ? "border-primary-line bg-primary-soft/50" : "border-line bg-surface",
              )}
              {...mark({
                title: i === 0 ? `Today, ${fmtDate(d.date)}` : fmtDate(d.date),
                lines: total ? d.parts.filter((p) => p.value).map((p) => `${p.value} ${p.label}`) : ["Nothing scheduled"],
              })}
            >
              <span className="text-[11px] text-ink-subtle">{day.toLocaleDateString("en-US", { weekday: "short" })}</span>
              <span className={cx("tabular text-sm font-semibold", i === 0 ? "text-primary-ink" : "text-ink")}>{day.getDate()}</span>
              <span key={total} className={cx("mt-auto flex flex-wrap gap-0.5 pt-1", total > 0 && "animate-pop")} aria-hidden>
                {d.parts.flatMap((p) => Array.from({ length: Math.min(p.value, 6) }, (_, k) => <span key={p.label + k} className={cx("h-2 w-2 rounded-full", FILL[p.tone])} />))}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/* ------------------------------------------------------------------ windows */

export type WindowRow = { key: string; label: string; from: string; tip: Tip; onOpen?: () => void };

/**
 * When each record may next be acted on, over the coming days: a cool-down until the
 * first allowed day, then open. Shows a rule; it never offers a way around it.
 */
export function WindowChart({ rows, today, days = 21, label }: { rows: WindowRow[]; today: string; days?: number; label: string }) {
  const mark = useMark();
  const start = toDate(today).getTime();
  const offset = (d: string) => Math.max(0, Math.min(days, Math.round((toDate(d).getTime() - start) / 86_400_000)));
  const ticks = [0, 7, 14, days];
  return (
    <div role="group" aria-label={label}>
      <div className="mb-1 grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-3 text-[11px] text-ink-subtle">
        <span />
        <span className="relative h-4">
          {ticks.map((t) => (
            <span key={t} className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: `${(t / days) * 100}%` }}>
              {t === 0 ? "Today" : `+${t} d`}
            </span>
          ))}
        </span>
      </div>
      <ul className="space-y-1.5">
        {rows.map((r) => {
          const o = offset(r.from);
          return (
            <li key={r.key} className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] items-center gap-3">
              <span className="truncate text-[13px] text-ink" title={r.label}>
                {r.label}
              </span>
              <div
                data-draw="x"
                className="relative flex h-4 gap-[2px] overflow-hidden rounded outline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
                {...mark(r.tip, r.onOpen)}
              >
                {o > 0 && <span className="chart-hatch h-full rounded-l" style={{ width: `${(o / days) * 100}%` }} />}
                {o < days && <span className={cx("h-full flex-1 bg-primary", o === 0 ? "rounded" : "rounded-r")} />}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ steps */

export type Step = { key: string; label: string; state: "done" | "current" | "todo" | "skipped"; date?: string | null };

const STEP_ICON = {
  done: <Check className="h-3.5 w-3.5" aria-hidden />,
  current: <span className="h-2 w-2 rounded-full bg-on-primary" aria-hidden />,
  todo: null,
  skipped: <Minus className="h-3.5 w-3.5" aria-hidden />,
};
const STEP_TONE = {
  done: "bg-ok-fill text-on-primary",
  current: "bg-primary text-on-primary ring-4 ring-primary-soft",
  todo: "border-2 border-line-strong bg-surface",
  skipped: "bg-sunken text-ink-subtle",
};
const STEP_WORD = { done: "Done", current: "Now", todo: "Next", skipped: "Not needed" };

/**
 * When the current step moved since this viewer last saw it (`seenKey`, for example the
 * request id), the steps reached since then fill in and the new current step pops, once.
 */
function useStepAdvance(scope: RefObject<HTMLOListElement | null>, steps: Step[], seenKey?: string) {
  const { user } = useAuth();
  const reached = steps.filter((s) => s.state === "done" || s.state === "current").length;
  useGSAP(
    () => {
      const root = scope.current;
      if (!root || !seenKey || !user) return;
      const key = `steps.${user.id}.${seenKey}`;
      const before = Number(preferencesSafe(key));
      const moved = changedSinceSeen(key, String(reached));
      if (!moved || reducedMotion() || !(reached > before)) return;
      const nodes = Array.from(root.querySelectorAll<HTMLElement>("[data-step-node]")).slice(Math.max(0, before - 1), reached);
      if (!nodes.length) return;
      // Rail fill: the line grows from where it stood at the last visit to the new step,
      // then the newly reached nodes settle in.
      const rail = root.parentElement?.querySelector<HTMLElement>("[data-step-rail]");
      if (rail && reached > 1) gsap.from(rail, { scaleY: Math.max(0, before - 1) / (reached - 1), duration: 0.45, ease: "power2.inOut", transformOrigin: "50% 0%", clearProps: "transform" });
      gsap.from(nodes, { scale: 0.6, opacity: 0.3, duration: DUR.standard, stagger: 0.12, delay: rail ? 0.2 : 0, ease: "back.out(2)", clearProps: "transform,opacity" });
    },
    { scope, dependencies: [reached, seenKey] },
  );
}

/** Where a request stands: one step per stage, with the date it happened. Horizontal from
 *  640px unless `vertical` (narrow columns). `seenKey` animates a step change once. */
export function Steps({ steps, label, vertical, seenKey }: { steps: Step[]; label: string; vertical?: boolean; seenKey?: string }) {
  const ref = useRef<HTMLOListElement>(null);
  useStepAdvance(ref, steps, seenKey);
  if (vertical) {
    // A rail behind the nodes, filled up to the furthest step reached (done, current or
    // skipped), so progress reads at a glance as well as in the words beside each step.
    const last = steps.reduce((m, st, i) => (st.state === "todo" ? m : i), 0);
    const fill = steps.length > 1 ? last / (steps.length - 1) : 0;
    return (
      <div className="relative">
        <span aria-hidden className="absolute bottom-2.5 left-[9.5px] top-2.5 w-px bg-line-strong" />
        <span
          aria-hidden
          data-step-rail
          className="absolute left-[9px] top-2.5 w-0.5 rounded-full bg-ok-fill"
          style={{ height: `calc((100% - 20px) * ${fill})` }}
        />
      <ol ref={ref} aria-label={label} className="relative space-y-1.5">
        {steps.map((s) => (
          <li key={s.key} className="flex items-center gap-2">
            <span data-step-node className={cx("grid h-5 w-5 shrink-0 place-items-center rounded-full [&>svg]:h-3 [&>svg]:w-3", STEP_TONE[s.state])} aria-hidden>
              {STEP_ICON[s.state]}
            </span>
            <span className={cx("min-w-0 flex-1 text-[13px] leading-5", s.state === "current" ? "font-semibold text-ink" : s.state === "todo" ? "text-ink-subtle" : "text-ink-muted")}>
              {s.label}
              <span className="sr-only">: {STEP_WORD[s.state]}</span>
            </span>
            <span className="tabular shrink-0 text-xs text-ink-subtle">
              {s.date ? shortDate(s.date) : s.state === "skipped" ? "Not needed" : s.state === "current" ? "Now" : ""}
            </span>
          </li>
        ))}
      </ol>
      </div>
    );
  }
  return (
    <ol ref={ref} aria-label={label} className="flex flex-col gap-2 sm:flex-row sm:gap-0">
      {steps.map((s, i) => (
        <li key={s.key} className="flex min-w-0 items-start gap-2 sm:flex-1 sm:flex-col sm:items-center sm:text-center">
          <div className="flex w-full items-center sm:justify-center">
            <span className={cx("hidden h-0.5 flex-1 sm:block", i === 0 ? "invisible" : s.state === "todo" ? "bg-line" : "bg-ok-fill")} aria-hidden />
            <span data-step-node className={cx("grid h-6 w-6 shrink-0 place-items-center rounded-full", STEP_TONE[s.state])} aria-hidden>
              {STEP_ICON[s.state]}
            </span>
            <span className={cx("hidden h-0.5 flex-1 sm:block", i === steps.length - 1 ? "invisible" : steps[i + 1].state === "todo" ? "bg-line" : "bg-ok-fill")} aria-hidden />
          </div>
          <div className="min-w-0 sm:mt-1.5 sm:px-1">
            <p className={cx("text-[13px] leading-5", s.state === "current" ? "font-semibold text-ink" : s.state === "todo" ? "text-ink-subtle" : "text-ink-muted")}>
              {s.label}
              <span className="sr-only">: {STEP_WORD[s.state]}</span>
            </p>
            <p className="text-xs text-ink-subtle">{s.date ? fmtDate(s.date) : s.state === "skipped" ? "Not needed" : s.state === "current" ? "In progress" : ""}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

/* ------------------------------------------------------------------ streak */

/** A streak, with its exact definition always one hover or focus away. */
export function StreakCard({ streak, label, empty }: { streak: Json | null | undefined; label: string; empty: string }) {
  const [open, setOpen] = useState(false);
  const { user } = useAuth();
  const figure = useRef<HTMLSpanElement>(null);
  const tile = useRef<HTMLSpanElement>(null);
  const current = streak ? String(streak.value) : null;
  // A change since this viewer last saw the streak: the new number slides in (up when it
  // grew, a plain fade when it restarted). First sight, reloads and no change: static.
  useGSAP(
    () => {
      if (current === null || !user || !figure.current) return;
      const key = `streak.${user.id}.${label}`;
      const before = Number(preferencesSafe(key));
      if (!changedSinceSeen(key, current) || reducedMotion()) return;
      const grew = Number(current) > before;
      gsap.from(figure.current, { yPercent: grew ? 60 : 0, opacity: 0, duration: DUR.standard, clearProps: "transform,opacity" });
      if (grew && tile.current) gsap.from(tile.current, { scale: 0.85, duration: DUR.standard, ease: "back.out(2)", clearProps: "transform" });
    },
    { dependencies: [current, label] },
  );
  if (!streak) {
    return (
      <div className="flex items-center gap-3 rounded-lg bg-subtle px-4 py-3 text-sm text-ink-muted">
        <Repeat className="h-4 w-4 shrink-0 text-ink-subtle" aria-hidden />
        {empty}
      </div>
    );
  }
  const value = `${num(streak.value)}${streak.capped ? "+" : ""}`;
  return (
    <div className="rounded-lg bg-subtle px-4 py-3">
      <div className="flex items-center gap-3">
        <span ref={tile} className={cx("grid h-9 w-9 shrink-0 place-items-center rounded-lg", streak.value ? "bg-ok-soft text-ok" : "bg-sunken text-ink-subtle")} aria-hidden>
          <Repeat className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[13px] text-ink-muted">{label}</p>
          <p className="tabular overflow-hidden text-lg font-semibold leading-6 text-ink">
            <span ref={figure} className="inline-block">
              {value}
            </span>{" "}
            <span className="text-sm font-normal text-ink-muted">{streak.unit}</span>
            {!streak.value && <span className="ml-2 text-xs font-normal text-ink-muted">Starts again with the next one</span>}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="shrink-0 rounded-md px-2 py-1 text-xs font-semibold text-primary-ink hover:bg-primary-soft focus-visible:outline-2 focus-visible:outline-focus"
        >
          How it is counted
        </button>
      </div>
      {open && <p className="mt-2 text-xs leading-5 text-ink-muted">{streak.definition}</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ trend lines */

export type TrendPoint = { t: number; value: number; tip: Tip };
export type TrendSeries = { key: string; label: string; tone: ChartTone; points: TrendPoint[]; dashed?: boolean };

const SERIES_STROKE: Record<ChartTone, string> = {
  brand: "var(--series-1)",
  info: "var(--series-1)",
  warn: "var(--series-2)",
  ok: "var(--series-3)",
  bad: "var(--bad-fill)",
  neutral: "var(--series-baseline)",
};

/**
 * One or more series over time on one scale (never two y axes). Points sit at their real
 * time, so gaps between readings stay visible; an optional band marks a target range.
 * Every point is a mark with its own tooltip; the newest point of each series is larger.
 */
export function TrendLines({
  series,
  from,
  to,
  unit,
  bands = [],
  label,
  height = 200,
  xTicks,
}: {
  series: TrendSeries[];
  from: number;
  to: number;
  unit?: string;
  bands?: Array<{ low?: number | null; high?: number | null; label: string }>;
  label: string;
  height?: number;
  xTicks: Array<{ t: number; label: string }>;
}) {
  const mark = useMark();
  const W = 640;
  const H = height;
  const L = 44;
  const R = 14;
  const T = 12;
  const B = 26;
  const values = series.flatMap((s) => s.points.map((p) => p.value));
  bands.forEach((b) => {
    if (b.low != null) values.push(b.low);
    if (b.high != null) values.push(b.high);
  });
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const pad = (hi - lo) * 0.12;
  lo = lo - pad;
  hi = hi + pad;
  const x = (t: number) => L + ((t - from) / Math.max(1, to - from)) * (W - L - R);
  const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const ticks = [0, 0.5, 1].map((f) => lo + f * (hi - lo));
  const fmt = (v: number) => (Math.abs(hi - lo) < 10 ? v.toFixed(1) : String(Math.round(v)));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full overflow-visible" role="group" aria-label={label}>
      {bands.length > 0 && (
        <g data-draw="fade">
          {bands.map((b) => (
            <g key={b.label}>
              <rect
                x={L}
                width={W - L - R}
                y={y(b.high ?? hi)}
                height={Math.max(0, y(b.low ?? lo) - y(b.high ?? hi))}
                fill="var(--ok-soft)"
                fillOpacity={0.7}
                stroke="var(--ok-line)"
                strokeDasharray="3 3"
              />
              <text x={W - R - 4} y={y(b.high ?? hi) + 13} textAnchor="end" fontSize={11} fill="var(--ok)">
                {b.label}
              </text>
            </g>
          ))}
        </g>
      )}
      {ticks.map((v) => (
        <g key={v}>
          <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="var(--chart-grid)" />
          <text x={L - 6} y={y(v) + 4} textAnchor="end" fontSize={11} fill="var(--chart-text)">
            {fmt(v)}
          </text>
        </g>
      ))}
      {unit && (
        <text x={4} y={T + 2} fontSize={10} fill="var(--chart-text)">
          {unit}
        </text>
      )}
      {xTicks.map((tk) => (
        <text key={tk.t} x={x(tk.t)} y={H - 6} textAnchor="middle" fontSize={11} fill="var(--chart-text)">
          {tk.label}
        </text>
      ))}
      {series.map((s) => (
        <path
          key={s.key}
          data-draw="line"
          d={s.points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)} ${y(p.value).toFixed(1)}`).join("")}
          fill="none"
          stroke={SERIES_STROKE[s.tone]}
          strokeWidth={2}
          strokeDasharray={s.dashed ? "5 4" : undefined}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      ))}
      <g data-draw="fade">
        {series.map((s) =>
          s.points.map((p, i) => (
            <circle
              key={`${s.key}-${p.t}-${i}`}
              cx={x(p.t)}
              cy={y(p.value)}
              r={i === s.points.length - 1 ? 4.5 : 3.5}
              fill={SERIES_STROKE[s.tone]}
              stroke="var(--surface)"
              strokeWidth={2}
              className="focus-visible:outline-none"
              {...mark(p.tip, undefined, s.points.length * series.length <= 40)}
            />
          )),
        )}
      </g>
    </svg>
  );
}

/** Legend for line series: a short line in the series' stroke and its name. */
export function SeriesLegend({ series }: { series: Array<{ key: string; label: string; tone: ChartTone; dashed?: boolean }> }) {
  return (
    <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
      {series.map((s) => (
        <span key={s.key} className="inline-flex items-center gap-1.5">
          <svg width="18" height="6" aria-hidden>
            <line x1="1" x2="17" y1="3" y2="3" stroke={SERIES_STROKE[s.tone]} strokeWidth="2.5" strokeDasharray={s.dashed ? "4 3" : undefined} strokeLinecap="round" />
          </svg>
          {s.label}
        </span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ stacked columns */

export type ColumnStack = { key: string; label: string; parts: Array<{ key: string; label: string; value: number; tone: ChartTone }>; tip?: Tip };

/** Columns over a category (a day, a week), each split into parts that add up to the total.
 *  Bars grow from the baseline; the total sits above each column. */
export function StackedColumns({ columns, height = 150, label }: { columns: ColumnStack[]; height?: number; label: string }) {
  const mark = useMark();
  const max = Math.max(1, ...columns.map((c) => c.parts.reduce((n, p) => n + p.value, 0)));
  const inner = height - 22;
  return (
    <div role="group" aria-label={label}>
      <div className="flex items-end gap-2" style={{ height }}>
        {columns.map((c) => {
          const total = c.parts.reduce((n, p) => n + p.value, 0);
          return (
            <div key={c.key} className="flex h-full min-w-0 flex-1 flex-col justify-end">
              <span className="tabular mb-1 text-center text-xs font-semibold text-ink">{total || ""}</span>
              <div
                data-draw="y"
                className="flex flex-col-reverse gap-[2px] overflow-hidden rounded-t-[4px] outline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
                style={{ height: total ? Math.max(4, (total / max) * inner) : 2 }}
                {...mark(
                  c.tip ?? {
                    title: c.label,
                    lines: total ? c.parts.filter((p) => p.value).map((p) => `${p.label}: ${p.value}`) : ["Nothing recorded"],
                  },
                )}
              >
                {total ? (
                  c.parts
                    .filter((p) => p.value)
                    .map((p) => <div key={p.key} className={FILL[p.tone]} style={{ flexGrow: p.value, flexBasis: 0 }} />)
                ) : (
                  <div className="h-full bg-line" />
                )}
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-1.5 flex gap-2 border-t border-line pt-1.5">
        {columns.map((c) => (
          <span key={c.key} className="min-w-0 flex-1 truncate text-center text-[11px] leading-4 text-ink-subtle">
            {c.label}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Two measures side by side per category (for example routed and answered per week), on
 *  one scale. */
export function PairedColumns({
  columns,
  series,
  height = 140,
  label,
}: {
  columns: Array<{ key: string; label: string; values: [number, number]; tip: Tip }>;
  series: [{ label: string; tone: ChartTone }, { label: string; tone: ChartTone }];
  height?: number;
  label: string;
}) {
  const mark = useMark();
  const max = Math.max(1, ...columns.flatMap((c) => c.values));
  const inner = height - 8;
  return (
    <div role="group" aria-label={label}>
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
        {series.map((s) => (
          <span key={s.label} className="inline-flex items-center gap-1.5">
            <ToneKey tone={s.tone} /> {s.label}
          </span>
        ))}
      </div>
      <div className="flex items-end gap-2" style={{ height }}>
        {columns.map((c) => (
          <div
            key={c.key}
            className="flex h-full min-w-0 flex-1 items-end justify-center gap-[3px] rounded outline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
            {...mark(c.tip)}
          >
            {c.values.map((v, i) => (
              <div
                key={i}
                data-draw="y"
                className={cx("w-1/3 max-w-4 rounded-t-[3px]", FILL[series[i].tone])}
                style={{ height: v ? Math.max(3, (v / max) * inner) : 1, opacity: v ? 1 : 0.35 }}
              />
            ))}
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex gap-2 border-t border-line pt-1.5">
        {columns.map((c) => (
          <span key={c.key} className="min-w-0 flex-1 truncate text-center text-[11px] leading-4 text-ink-subtle">
            {c.label}
          </span>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ heatmap */

/** A grid of counts (rows by columns), darker for more. Every cell has a tooltip and is
 *  reachable by keyboard when the grid is small enough. */
export function Heatmap({
  rows,
  cols,
  values,
  tip,
  label,
  tone = "brand",
}: {
  rows: string[];
  cols: string[];
  values: number[][];
  tip: (row: number, col: number, value: number) => Tip;
  label: string;
  tone?: ChartTone;
}) {
  const mark = useMark();
  const max = Math.max(1, ...values.flat());
  const focusable = rows.length * cols.length <= 60;
  return (
    <div role="group" aria-label={label} className="scroll-quiet overflow-x-auto">
      <div className="grid min-w-[320px] gap-[3px]" style={{ gridTemplateColumns: `2.5rem repeat(${cols.length}, minmax(0, 1fr))` }}>
        <span />
        {cols.map((c) => (
          <span key={c} className="truncate text-center text-[11px] text-ink-subtle">
            {c}
          </span>
        ))}
        {rows.map((r, i) => (
          <div key={r} className="contents">
            <span className="self-center text-[11px] text-ink-subtle">{r}</span>
            {cols.map((c, j) => {
              const v = values[i]?.[j] ?? 0;
              return (
                <div
                  key={c}
                  className={cx("h-7 rounded-[4px] outline-offset-1 focus-visible:outline-2 focus-visible:outline-focus", v ? FILL[tone] : "bg-sunken")}
                  style={v ? { opacity: 0.18 + 0.82 * (v / max) } : undefined}
                  {...mark(tip(i, j, v), undefined, focusable)}
                />
              );
            })}
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-1.5 text-[11px] text-ink-subtle" aria-hidden>
        Fewer
        {[0.2, 0.45, 0.7, 1].map((o) => (
          <span key={o} className={cx("h-2.5 w-4 rounded-sm", FILL[tone])} style={{ opacity: o }} />
        ))}
        More
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ helpers */

/** The last value seen under a key, read before `changedSinceSeen` records the new one. */
function preferencesSafe(key: string) {
  return preferences.lastSeen(key) ?? "0";
}

/** "Oct 8" style label for axis ticks. */
export const shortDate = (d: string | null | undefined) => (d ? fmtDate(d).replace(/, \d{4}$/, "") : "");

/** Hours as a plain duration ("5 h", "2.5 days"). */
export function duration(hours: number) {
  if (hours < 1) return "under 1 h";
  if (hours < 48) return `${Math.round(hours)} h`;
  const days = hours / 24;
  return `${days < 10 ? days.toFixed(1) : Math.round(days)} days`;
}
