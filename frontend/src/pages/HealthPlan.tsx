// Health goal plans: the patient's own section on "My health" (today's check-ins, readings
// against the care team's targets, check-in calendar, coins, the opt-in board) and the care
// manager's plan editor on Patient 360. Every figure comes from the server
// (backend/app/clinical/plan.py); nothing here invents a reading, a target or a coin.
// Queries are keyed under "insights" so they refresh after every action and every minute.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Award, CalendarCheck, Coins, HeartPulse, Pill, Plus, Trophy } from "lucide-react";
import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, post, put } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { ChartPanel, ChartTable, Heatmap, SeriesLegend, TrendLines, shortDate } from "../charts";
import type { ChartTone, TrendSeries } from "../charts";
import { BentoGrid, BentoSplit, SectionHeader } from "../layout";
import { DUR, changedSinceSeen, gsap, reducedMotion, useGSAP } from "../motion";
import { useToast } from "../toast";
import {
  AnimatedNumber,
  Badge,
  Button,
  Card,
  ErrorNote,
  ErrorState,
  LoadingRows,
  Switch,
  TextField,
  cx,
  fmtDate,
  fmtDateTime,
  toDate,
} from "../ui";

const REFRESH = 60_000;
const TONES: ChartTone[] = ["brand", "warn", "ok"];
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/* ------------------------------------------------------------------ shared pieces */

/** Readings of every tracked measurement over the last 14 days, one chart per measurement,
 *  with the care team's target band where one is set. */
export function ReadingsCharts({ readings, query, title = "My health readings vs target" }: { readings: Json[]; query: { isLoading: boolean; error: unknown; refetch: () => unknown }; title?: string }) {
  if (!readings.length) return null;
  const now = Date.now();
  const from = now - 14 * 86_400_000;
  const xTicks = [14, 7, 0].map((d) => {
    const t = now - d * 86_400_000;
    return { t, label: d === 0 ? "Today" : shortDate(new Date(t).toISOString()) };
  });
  return (
    <>
      {readings.map((m) => {
        const series: TrendSeries[] = m.components.map((c: Json, i: number) => ({
          key: c.key,
          label: `${c.label} (${c.unit})`,
          tone: TONES[i % TONES.length],
          points: m.readings.map((r: Json) => ({
            t: toDate(r.taken_at).getTime(),
            value: r.values[c.key],
            tip: { title: `${c.label}: ${r.values[c.key]} ${c.unit}`, lines: [fmtDateTime(r.taken_at)] },
          })),
        }));
        const bands = m.components
          .filter((c: Json) => m.targets?.[c.key])
          .map((c: Json) => {
            const t = m.targets[c.key];
            const range = t.low != null && t.high != null ? `${t.low}–${t.high}` : t.low != null ? `≥ ${t.low}` : `≤ ${t.high}`;
            return { low: t.low, high: t.high, label: `${c.label} target ${range} ${c.unit}` };
          });
        const unit = [...new Set(m.components.map((c: Json) => c.unit))].join(", ");
        return (
          <ChartPanel
            key={m.key}
            span="half"
            title={`${title}: ${m.label.toLowerCase()}`}
            question={bands.length ? "Your readings over the last 14 days, with the target range your care team set." : "Your readings over the last 14 days. Your care team has not set a target for this one."}
            query={query}
            empty={!m.readings.length ? `No ${m.label.toLowerCase()} readings in the last 14 days.` : false}
            table={
              <ChartTable
                caption={`${m.label} readings`}
                head={["Taken", ...m.components.map((c: Json) => `${c.label} (${c.unit})`)]}
                rows={[...m.readings].reverse().map((r: Json) => [fmtDateTime(r.taken_at), ...m.components.map((c: Json) => r.values[c.key])])}
              />
            }
          >
            {series.length > 1 && <SeriesLegend series={series} />}
            <TrendLines series={series} from={from} to={now} unit={unit} bands={bands} xTicks={xTicks} label={`${m.label} readings over the last 14 days`} />
          </ChartPanel>
        );
      })}
    </>
  );
}

/** Completed check-ins per day, five weeks, darker for more; coin days marked. */
export function CheckinCalendar({ calendar, query }: { calendar: Json | null; query: { isLoading: boolean; error: unknown; refetch: () => unknown } }) {
  if (!calendar) return null;
  const days: Json[] = calendar.days;
  // Columns are weeks (Monday start), rows are weekdays.
  const first = toDate(days[0].date);
  const offset = (first.getDay() + 6) % 7;
  const weeks = Math.ceil((days.length + offset) / 7);
  const grid = WEEKDAYS.map(() => Array.from({ length: weeks }, () => -1));
  const cellDay: Array<Array<Json | null>> = WEEKDAYS.map(() => Array.from({ length: weeks }, () => null));
  days.forEach((d, i) => {
    const k = i + offset;
    grid[k % 7][Math.floor(k / 7)] = d.before_plan ? 0 : d.count;
    cellDay[k % 7][Math.floor(k / 7)] = d;
  });
  // Each column is labelled with the Monday that starts its week.
  const cols = Array.from({ length: weeks }, (_, w) => shortDate(new Date(first.getTime() + (w * 7 - offset) * 86_400_000).toISOString()));
  const values = grid.map((row) => row.map((v) => Math.max(0, v)));
  return (
    <ChartPanel
      span="half"
      title="Check-in streak"
      question={`Plan items you completed each day, out of ${calendar.items}; your daily goal is ${calendar.quota}. Darker means more.`}
      query={query}
      table={
        <ChartTable
          caption="Check-ins per day"
          head={["Day", "Completed", "Coin"]}
          rows={[...days].reverse().filter((d) => !d.before_plan).map((d) => [fmtDate(d.date), `${d.count} of ${calendar.quota}`, d.coin ? "Yes" : "—"])}
        />
      }
    >
      <Heatmap
        label="Check-ins per day over five weeks"
        rows={WEEKDAYS}
        cols={cols}
        values={values}
        tone="ok"
        tip={(r, c, v) => {
          const d = cellDay[r][c];
          if (!d) return { title: "Outside this period" };
          if (d.before_plan) return { title: fmtDate(d.date), lines: ["Before your plan started"] };
          return {
            title: fmtDate(d.date),
            lines: [`${v} of ${calendar.quota} check-ins`, d.coin ? "Coin earned" : v >= calendar.quota ? null : "Goal not reached"],
          };
        }}
      />
    </ChartPanel>
  );
}

/* ------------------------------------------------------------------ patient */

/** Today: what the plan asks, what is done, and a quick way to log each item. */
function TodayCheckins({ data }: { data: Json }) {
  const client = useQueryClient();
  const toast = useToast();
  const plan = data.plan;
  const progress = data.progress;
  const [open, setOpen] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const refresh = (r: Json) => {
    void client.invalidateQueries({ queryKey: ["insights"] });
    if (r?.progress?.coin_today && !progress.coin_today) toast("Daily check-in goal reached. You earned a coin.");
  };
  const reading = useMutation({
    mutationFn: (m: Json) =>
      post("/me/readings", {
        measure: m.key,
        values: Object.fromEntries(m.components.map((c: Json) => [c.key, Number(values[`${m.key}.${c.key}`])])),
      }),
    onSuccess: (r) => {
      setOpen(null);
      setValues({});
      refresh(r);
    },
  });
  const medication = useMutation({ mutationFn: () => post("/me/checkins/medication", {}), onSuccess: refresh });
  const done = new Set<string>(progress.done);
  const submit = (m: Json) => (e: FormEvent) => {
    e.preventDefault();
    reading.mutate(m);
  };
  const pct = Math.min(1, progress.done.length / Math.max(1, progress.quota));
  return (
    <Card
      title="Today's check-ins"
      description={`Your care team's plan: complete ${progress.quota} of ${plan.items.length} item${plan.items.length === 1 ? "" : "s"} a day.`}
      action={
        progress.coin_today ? (
          <Badge tone="ok" icon={<Coins className="h-3.5 w-3.5" aria-hidden />}>
            Coin earned today
          </Badge>
        ) : (
          <Badge tone="neutral">
            {progress.done.length} of {progress.quota} done
          </Badge>
        )
      }
    >
      <div className="mb-4 h-2 overflow-hidden rounded-full bg-sunken" role="progressbar" aria-label="Today's check-ins" aria-valuemin={0} aria-valuemax={progress.quota} aria-valuenow={progress.done.length}>
        <div className="h-full w-full origin-left rounded-full bg-ok-fill transition-transform duration-500" style={{ transform: `scaleX(${pct})` }} />
      </div>
      <ul className="divide-y divide-line">
        {plan.measures.map((m: Json) => (
          <li key={m.key} className="py-3 first:pt-0">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="flex items-center gap-2 text-sm font-semibold text-ink">
                <HeartPulse className="h-4 w-4 text-ink-subtle" aria-hidden /> {m.label}
                {done.has(m.key) && <Badge tone="ok">Done today</Badge>}
              </span>
              <Button size="sm" variant={done.has(m.key) ? "ghost" : "secondary"} onClick={() => setOpen(open === m.key ? null : m.key)}>
                <Plus className="h-3.5 w-3.5" aria-hidden /> {done.has(m.key) ? "Add another reading" : "Log reading"}
              </Button>
            </div>
            {open === m.key && (
              <form onSubmit={submit(m)} className="mt-3 flex flex-wrap items-end gap-3">
                {m.components.map((c: Json) => (
                  <TextField
                    key={c.key}
                    label={`${c.label} (${c.unit})`}
                    type="number"
                    inputMode="decimal"
                    min={c.min}
                    max={c.max}
                    step={c.step}
                    required
                    className="w-36"
                    value={values[`${m.key}.${c.key}`] ?? ""}
                    onChange={(e) => setValues((v) => ({ ...v, [`${m.key}.${c.key}`]: e.target.value }))}
                  />
                ))}
                <Button type="submit" variant="primary" busy={reading.isPending}>
                  Save reading
                </Button>
              </form>
            )}
          </li>
        ))}
        {plan.medication_check && (
          <li className="flex flex-wrap items-center justify-between gap-2 py-3 last:pb-0">
            <span className="flex items-center gap-2 text-sm font-semibold text-ink">
              <Pill className="h-4 w-4 text-ink-subtle" aria-hidden /> {data.medication_label}
              {done.has("medication") && <Badge tone="ok">Done today</Badge>}
            </span>
            {!done.has("medication") && (
              <Button size="sm" busy={medication.isPending} done={medication.isSuccess} onClick={() => medication.mutate()}>
                Confirm
              </Button>
            )}
          </li>
        )}
      </ul>
      <ErrorNote error={reading.error ?? medication.error} />
      <p className="mt-3 text-xs text-ink-subtle">
        Readings are for your care team; tell them, or call your doctor, if a reading worries you. Coins count completed check-ins only and say nothing about the readings.
      </p>
    </Card>
  );
}

/** The coin balance, milestone badges and recent coins. A newly earned badge pops once. */
function CoinCard({ coins }: { coins: Json }) {
  const { user } = useAuth();
  const ref = useRef<HTMLDivElement>(null);
  const earned = coins.badges.filter((b: Json) => b.earned).length;
  useGSAP(
    () => {
      if (!user || !ref.current) return;
      const changed = changedSinceSeen(`badges.${user.id}`, String(earned));
      if (!changed || reducedMotion()) return;
      const last = ref.current.querySelectorAll("[data-badge-earned]");
      if (last.length) gsap.from(last[last.length - 1], { scale: 0.4, rotation: -20, duration: DUR.expressive, ease: "back.out(2)", clearProps: "transform" });
    },
    { dependencies: [earned], scope: ref },
  );
  const next = coins.next_badge;
  return (
    <Card title="Check-in coins" description="One coin for each day you complete your daily check-in goal.">
      <div ref={ref}>
        <div className="flex items-center gap-4">
          <span className="grid h-14 w-14 shrink-0 place-items-center rounded-2xl bg-warn-soft text-warn ring-1 ring-warn-line" aria-hidden>
            <Coins className="h-7 w-7" />
          </span>
          <div>
            <p className="tabular text-3xl font-semibold leading-9 text-ink">
              <AnimatedNumber value={coins.balance} />
            </p>
            <p className="text-[13px] text-ink-muted">{next ? `${next - coins.balance} more to your ${next}-coin badge` : "Every badge earned"}</p>
          </div>
        </div>
        <ul className="mt-4 grid grid-cols-4 gap-2" aria-label="Badges">
          {coins.badges.map((b: Json) => (
            <li
              key={b.at}
              {...(b.earned ? { "data-badge-earned": "" } : {})}
              className={cx(
                "flex flex-col items-center gap-1 rounded-xl px-1 py-2 text-center ring-1",
                b.earned ? "bg-ok-soft text-ok ring-ok-line" : "bg-subtle text-ink-subtle ring-line",
              )}
            >
              <Award className="h-5 w-5" aria-hidden />
              <span className="tabular text-xs font-semibold">{b.at}</span>
              <span className="sr-only">{b.earned ? "earned" : "not yet earned"}</span>
            </li>
          ))}
        </ul>
        {coins.history.length > 0 && (
          <p className="mt-3 text-xs text-ink-subtle">Latest coin: {fmtDate(coins.history[0].day)}</p>
        )}
      </div>
    </Card>
  );
}

/** The opt-in monthly board: aliases and countries only. */
function Board() {
  const client = useQueryClient();
  const board = useQuery({ queryKey: ["insights", "board"], queryFn: () => api<Json>("/me/board"), refetchInterval: REFRESH });
  const toggle = useMutation({
    mutationFn: (opted_in: boolean) => put("/me/board", { opted_in }),
    onSuccess: (data) => client.setQueryData(["insights", "board"], data),
  });
  const b = board.data;
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Trophy className="h-4 w-4 text-ink-subtle" aria-hidden /> This month's check-in board
        </span>
      }
      description="Optional. You appear only under a random alias with your country; never your name or anything about your health."
      action={b && <Switch label="Show me on the board" checked={b.opted_in} disabled={toggle.isPending} onChange={(v) => toggle.mutate(v)} />}
    >
      {board.isLoading ? (
        <LoadingRows rows={3} label="Loading the board" />
      ) : board.error ? (
        <ErrorState error={board.error} retry={() => void board.refetch()} title="The board could not be loaded" />
      ) : !b.opted_in ? (
        <p className="text-sm text-ink-muted">You are not on the board. Turn it on to see how your check-ins compare this month.</p>
      ) : (
        <>
          <p className="mb-3 text-[13px] text-ink-muted">
            You appear as <span className="font-semibold text-ink">{b.alias}</span>. {b.participants} taking part this month.
          </p>
          <ol className="divide-y divide-line">
            {b.entries.map((e: Json) => (
              <li key={e.alias} className={cx("flex items-center gap-3 py-2 text-sm", e.you && "font-semibold text-primary-ink")}>
                <span className="tabular w-6 text-ink-subtle">{e.rank}</span>
                <span className="min-w-0 flex-1 truncate">
                  {e.alias}
                  {e.you && " (you)"}
                </span>
                <span className="text-xs text-ink-subtle">{e.country ?? ""}</span>
                <span className="tabular w-10 text-right">{e.coins}</span>
              </li>
            ))}
          </ol>
        </>
      )}
      <ErrorNote error={toggle.error} />
    </Card>
  );
}

/**
 * The patient's health plan section. Always shown: with no plan yet it explains who sets one
 * up and offers to contact the care manager; once the plan exists everything fills in by
 * itself (the query refreshes every minute and after every action).
 */
export function HealthPlanSection({ onContact }: { onContact: () => void }) {
  const q = useQuery({ queryKey: ["insights", "plan"], queryFn: () => api<Json>("/me/plan"), refetchInterval: REFRESH });
  const coins = useQuery({ queryKey: ["insights", "coins"], queryFn: () => api<Json>("/me/coins"), refetchInterval: REFRESH, enabled: Boolean(q.data?.plan) });
  const d = q.data;
  return (
    <section aria-labelledby="plan-title" className="mb-8">
      <SectionHeader title="Your health plan" description="Check-ins, readings and goals your care manager sets up with you." className="mt-0" />
      <h2 id="plan-title" className="sr-only">
        Your health plan
      </h2>
      {q.isLoading ? (
        <Card>
          <LoadingRows rows={3} label="Loading your health plan" />
        </Card>
      ) : q.error ? (
        <ErrorState error={q.error} retry={() => void q.refetch()} title="Your health plan could not be loaded" />
      ) : !d.plan ? (
        // An invitation, not a placeholder: one compact row, so it never holds a screen of space.
        <Card>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
            <span aria-hidden className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-primary-soft text-primary-ink">
              <CalendarCheck className="h-5 w-5" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[15px] font-semibold text-ink">Your health journey starts here.</p>
              <p className="mt-0.5 max-w-3xl text-sm text-ink-subtle">
                {d.has_care_manager
                  ? `Your care manager${d.care_managers.length ? ` (${d.care_managers.join(", ")})` : ""} will help you set up a personalised goal plan to track the health measurements that matter to you. Your check-ins, readings and coins appear here once it is ready.`
                  : "A care manager needs to be assigned to you before your plan can be set up. Your care team will arrange this; there is nothing you need to do."}
              </p>
            </div>
            {d.has_care_manager && (
              <Button variant="primary" className="shrink-0 self-start sm:self-center" onClick={onContact}>
                Contact your care manager
              </Button>
            )}
          </div>
        </Card>
      ) : (
        <>
          {coins.data ? <BentoSplit main={<TodayCheckins data={d} />} aside={<CoinCard coins={coins.data} />} /> : <TodayCheckins data={d} />}
          <BentoGrid className="mt-4 xl:[&>*:last-child:nth-child(odd)]:col-span-12">
            <CheckinCalendar calendar={d.calendar} query={q} />
            <ReadingsCharts readings={d.readings} query={q} />
          </BentoGrid>
          <div className="mt-4">
            <Board />
          </div>
        </>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ care manager */

type Draft = { measures: Record<string, Record<string, { low: string; high: string }>>; medication: boolean; quota: number };

const toDraft = (plan: Json | null): Draft => ({
  measures: Object.fromEntries(
    (plan?.measures ?? []).map((m: Json) => [
      m.key,
      Object.fromEntries(m.components.map((c: Json) => [c.key, { low: m.targets?.[c.key]?.low ?? "", high: m.targets?.[c.key]?.high ?? "" }])),
    ]),
  ),
  medication: Boolean(plan?.medication_check),
  quota: plan?.checkin_quota ?? 1,
});

/** The care manager sets the patient's plan and sees what the patient recorded. */
export function CarePlanEditor({ patientId }: { patientId: string }) {
  const client = useQueryClient();
  const toast = useToast();
  const key = ["insights", "care-plan", patientId];
  const q = useQuery({ queryKey: key, queryFn: () => api<Json>(`/care/patients/${patientId}/plan`), refetchInterval: REFRESH });
  const [draft, setDraft] = useState<Draft | null>(null);
  const save = useMutation({
    mutationFn: (d: Draft) =>
      put(`/care/patients/${patientId}/plan`, {
        measures: Object.entries(d.measures).map(([k, comps]) => ({
          key: k,
          targets: Object.fromEntries(
            Object.entries(comps)
              .filter(([, t]) => t.low !== "" || t.high !== "")
              .map(([c, t]) => [c, { low: t.low === "" ? null : Number(t.low), high: t.high === "" ? null : Number(t.high) }]),
          ),
        })),
        medication_check: d.medication,
        checkin_quota: d.quota,
      }),
    onSuccess: (data) => {
      client.setQueryData(key, data);
      void client.invalidateQueries({ queryKey: ["insights"] });
      setDraft(null);
      toast("Health goal plan saved. The patient sees it now.");
    },
  });
  if (q.isLoading) return <Card title="Health goal plan"><LoadingRows rows={3} label="Loading the plan" /></Card>;
  if (q.error) return <ErrorState error={q.error} retry={() => void q.refetch()} title="The plan could not be loaded" />;
  const d = q.data;
  const plan = d.plan;
  const items = draft ? Object.keys(draft.measures).length + (draft.medication ? 1 : 0) : 0;
  return (
    <section aria-label="Health goal plan" className="mt-6">
      <Card
        title="Health goal plan"
        description="What the patient tracks, any target ranges, and how many check-ins a day earn their daily goal. Only the care team sets this."
        action={!draft && <Button size="sm" onClick={() => setDraft(toDraft(plan))}>{plan ? "Edit plan" : "Set up plan"}</Button>}
      >
        {draft ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate(draft);
            }}
            className="space-y-4"
          >
            <fieldset>
              <legend className="mb-2 text-sm font-semibold text-ink">Measurements to track</legend>
              <ul className="space-y-3">
                {d.catalogue.map((m: Json) => {
                  const on = Boolean(draft.measures[m.key]);
                  return (
                    <li key={m.key} className="rounded-lg border border-line p-3">
                      <label className="flex items-center gap-2 text-sm font-medium text-ink">
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={(e) =>
                            setDraft((x) => {
                              const measures = { ...x!.measures };
                              if (e.target.checked) measures[m.key] = Object.fromEntries(m.components.map((c: Json) => [c.key, { low: "", high: "" }]));
                              else delete measures[m.key];
                              return { ...x!, measures };
                            })
                          }
                        />
                        {m.label}
                      </label>
                      {on && (
                        <div className="mt-2 grid gap-3 sm:grid-cols-2">
                          {m.components.map((c: Json) => (
                            <div key={c.key} className="flex items-end gap-2">
                              {(["low", "high"] as const).map((b) => (
                                <TextField
                                  key={b}
                                  label={`${c.label} ${b === "low" ? "from" : "to"} (${c.unit})`}
                                  type="number"
                                  min={c.min}
                                  max={c.max}
                                  step={c.step}
                                  className="w-full"
                                  hint={b === "low" ? "Optional target" : undefined}
                                  value={draft.measures[m.key][c.key][b]}
                                  onChange={(e) =>
                                    setDraft((x) => ({
                                      ...x!,
                                      measures: {
                                        ...x!.measures,
                                        [m.key]: { ...x!.measures[m.key], [c.key]: { ...x!.measures[m.key][c.key], [b]: e.target.value } },
                                      },
                                    }))
                                  }
                                />
                              ))}
                            </div>
                          ))}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </fieldset>
            <label className="flex items-center gap-2 text-sm text-ink">
              <input type="checkbox" checked={draft.medication} onChange={(e) => setDraft((x) => ({ ...x!, medication: e.target.checked }))} />
              Daily "took my medicines" confirmation
            </label>
            <label className="block text-sm text-ink">
              <span className="font-semibold">Daily check-in goal</span>
              <select
                className="ml-3 h-10 rounded-lg border border-line bg-surface px-3 text-sm"
                value={Math.min(draft.quota, Math.max(1, items))}
                onChange={(e) => setDraft((x) => ({ ...x!, quota: Number(e.target.value) }))}
                disabled={!items}
              >
                {Array.from({ length: Math.max(1, items) }, (_, i) => i + 1).map((n) => (
                  <option key={n} value={n}>
                    {n} of {items || 1}
                  </option>
                ))}
              </select>
            </label>
            <ErrorNote error={save.error} />
            <div className="flex gap-2">
              <Button type="submit" variant="primary" busy={save.isPending} disabled={!items}>
                Save plan
              </Button>
              <Button variant="ghost" onClick={() => setDraft(null)}>
                Cancel
              </Button>
            </div>
          </form>
        ) : plan ? (
          <div className="space-y-2 text-sm text-ink">
            <p>
              Tracks {plan.measures.map((m: Json) => m.label.toLowerCase()).join(", ") || "no measurements"}
              {plan.medication_check ? " and the daily medicines confirmation" : ""}. Daily goal: {plan.checkin_quota} of {plan.items.length}.
            </p>
            {plan.measures.map((m: Json) =>
              Object.entries(m.targets ?? {}).map(([c, t]: [string, Json]) => (
                <p key={`${m.key}-${c}`} className="text-[13px] text-ink-muted">
                  {m.label}, {c}: target {t.low ?? "—"} to {t.high ?? "—"}
                </p>
              )),
            )}
            <p className="text-xs text-ink-subtle">
              Last changed {fmtDateTime(plan.updated_at)}
              {plan.updated_by ? ` by ${plan.updated_by}` : ""}.
              {d.progress && ` Today: ${d.progress.done.length} of ${d.progress.quota} check-ins.`}
            </p>
          </div>
        ) : (
          <p className="text-sm text-ink-muted">No plan yet. The patient sees an invitation to set one up with you.</p>
        )}
      </Card>
      {plan && (
        <BentoGrid className="mt-4 xl:[&>*:last-child:nth-child(odd)]:col-span-12">
          <CheckinCalendar calendar={d.calendar} query={q} />
          <ReadingsCharts readings={d.readings} query={q} title="Readings vs target" />
        </BentoGrid>
      )}
    </section>
  );
}
