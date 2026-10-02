import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Check, X } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, query } from "../api";
import type { Json } from "../api";
import {
  Badge,
  Card,
  Empty,
  ErrorNote,
  Field,
  Loading,
  PageHeader,
  SegmentBadge,
  StatusBadge,
  Table,
  channelName,
  cx,
  fmtDate,
  pct,
  titleCase,
} from "../ui";

const DAY = 86_400_000;
const WINDOW_DAYS = 365;

export function PatientList() {
  const [risk, setRisk] = useState("");
  const [q, setQ] = useState("");
  const list = useQuery({
    queryKey: ["patients", risk, q],
    queryFn: () => api(`/patients${query({ risk, q, limit: 100 })}`),
  });
  const byRisk: Record<string, number> = list.data?.by_risk ?? {};
  return (
    <>
      <PageHeader
        title="Patients"
        subtitle="Risk is recalculated every cycle from days covered, the current gap and the refill trend."
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {["", "high", "medium", "low"].map((r) => (
          <button
            key={r}
            onClick={() => setRisk(r)}
            className={cx(
              "rounded-full px-3 py-1.5 text-sm font-medium",
              risk === r
                ? "bg-brand-600 text-white"
                : "bg-white text-stone-600 ring-1 ring-inset ring-stone-200 hover:bg-stone-50",
            )}
          >
            {r ? `${titleCase(r)} risk` : "All"}
            <span className="tabular ml-1.5 opacity-70">
              {r ? (byRisk[r] ?? 0) : Object.values(byRisk).reduce((a, b) => a + b, 0)}
            </span>
          </button>
        ))}
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name or ID"
          className="ml-auto w-56 rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-sm"
        />
      </div>
      <Card>
        {list.isLoading ? (
          <Loading />
        ) : list.error ? (
          <ErrorNote error={list.error} />
        ) : !list.data.items.length ? (
          <Empty>No patients match.</Empty>
        ) : (
          <Table head={["Patient", "Risk", "Plan", "Location", "Preferred channel"]}>
            {list.data.items.map((p: Json) => (
              <tr key={p.patient_id} className="hover:bg-stone-50">
                <td className="px-3 py-2.5">
                  <Link to={`/patients/${p.patient_id}`} className="font-medium text-brand-700 hover:underline">
                    {p.name}
                  </Link>
                  <span className="ml-2 text-xs text-stone-400">{p.patient_id}</span>
                </td>
                <td className="px-3 py-2.5">
                  <SegmentBadge value={p.risk_segment} />
                </td>
                <td className="px-3 py-2.5 text-stone-600">{p.plan_type}</td>
                <td className="px-3 py-2.5 text-stone-600">
                  {p.city}, {p.state}
                </td>
                <td className="px-3 py-2.5 text-stone-600">{channelName(p.preferred_channel)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}

/** Covered [start, end) intervals in ms. An early refill is carried forward, as in the engine. */
function coverage(fills: Json[]): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  let cursor = 0;
  for (const f of [...fills].sort((a, b) => a.fill_date.localeCompare(b.fill_date))) {
    const start = Math.max(Date.parse(f.fill_date), cursor);
    cursor = start + f.days_supply * DAY;
    out.push([start, cursor]);
  }
  return out;
}

export function CoverageTimeline({ fills, asOf }: { fills: Json[]; asOf: string }) {
  const end = Date.parse(asOf) + DAY;
  const start = end - WINDOW_DAYS * DAY;
  const x = (t: number) => ((Math.min(Math.max(t, start), end) - start) / (end - start)) * 100;
  const intervals = coverage(fills).filter(([, e]) => e > start);
  const months: number[] = [];
  const first = new Date(start);
  for (let d = new Date(first.getFullYear(), first.getMonth() + 1, 1); d.getTime() < end; d.setMonth(d.getMonth() + 1)) {
    months.push(d.getTime());
  }
  return (
    <div>
      <div className="relative h-7 overflow-hidden rounded-md bg-red-50 ring-1 ring-inset ring-red-100">
        {intervals.map(([s, e], i) => (
          <div
            key={i}
            title={`Covered ${fmtDate(new Date(s).toISOString().slice(0, 10))} to ${fmtDate(new Date(e - DAY).toISOString().slice(0, 10))}`}
            className="absolute inset-y-0 border-r-2 border-white bg-brand-500"
            style={{ left: `${x(s)}%`, width: `${Math.max(x(e) - x(s), 0)}%` }}
          />
        ))}
        {fills
          .filter((f) => Date.parse(f.fill_date) >= start)
          .map((f, i) => (
            <div
              key={i}
              title={`Filled ${fmtDate(f.fill_date)} (${f.days_supply} days)`}
              className="absolute inset-y-0 w-0.5 bg-brand-900"
              style={{ left: `${x(Date.parse(f.fill_date))}%` }}
            />
          ))}
      </div>
      <div className="relative mt-1 h-4 text-[10px] text-stone-400">
        {months.map((m) => (
          <span key={m} className="absolute" style={{ left: `${x(m)}%` }}>
            {new Date(m).toLocaleDateString("en-GB", { month: "short" })}
          </span>
        ))}
      </div>
      <div className="mt-1 flex gap-4 text-xs text-stone-500">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-brand-500" /> Medication on hand
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-red-100 ring-1 ring-red-200" /> No supply
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-0.5 bg-brand-900" /> Fill
        </span>
      </div>
    </div>
  );
}

export function ChannelTable({ channels }: { channels: Record<string, Json> }) {
  return (
    <Table head={["Channel", "Sent", "Responded", "Response rate (smoothed)"]}>
      {Object.entries(channels).map(([name, c]) => (
        <tr key={name}>
          <td className="px-3 py-2">{channelName(name)}</td>
          <td className="tabular px-3 py-2">{c.sent}</td>
          <td className="tabular px-3 py-2">{c.engaged}</td>
          <td className="tabular px-3 py-2">{c.sent ? pct(c.rate) : "No history"}</td>
        </tr>
      ))}
    </Table>
  );
}

export function History({ items }: { items: Json[] }) {
  if (!items.length) return <Empty>No outreach on record.</Empty>;
  return (
    <Table head={["Date", "Type", "Channel", "Content", "Outcome", "Source"]}>
      {items.map((i) => (
        <tr key={i.id}>
          <td className="whitespace-nowrap px-3 py-2 text-stone-600">{fmtDate(i.ts)}</td>
          <td className="px-3 py-2">{titleCase(i.type_label)}</td>
          <td className="px-3 py-2 text-stone-600">{titleCase(i.channel_label)}</td>
          <td className="px-3 py-2 text-stone-600">{i.content_title ?? "—"}</td>
          <td className="px-3 py-2">
            <Badge
              tone={
                i.outcome === "filled"
                  ? "good"
                  : ["no_response", "declined"].includes(i.outcome)
                    ? "neutral"
                    : i.outcome === "pending"
                      ? "warn"
                      : "info"
              }
            >
              {titleCase(i.outcome)}
            </Badge>
          </td>
          <td className="px-3 py-2">
            {i.source === "nba" ? <Badge tone="brand">Engine</Badge> : <span className="text-xs text-stone-400">Earlier programme</span>}
          </td>
        </tr>
      ))}
    </Table>
  );
}

export function OpenNba({ nba }: { nba: Json | null }) {
  if (!nba) {
    return <p className="text-sm text-stone-500">No open recommendation. Nothing needs to be sent right now.</p>;
  }
  return (
    <Link to={`/nba/${nba.id}`} className="block rounded-lg border border-brand-100 bg-brand-50 p-3 hover:border-brand-500">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold text-brand-900">
          {titleCase(nba.action_label)} by {nba.channel_label}
        </span>
        <StatusBadge status={nba.status} />
      </div>
      <p className="mt-1 text-sm text-stone-700">{nba.block_reason ?? nba.rationale_summary ?? nba.timing_note}</p>
    </Link>
  );
}

export function PatientProfile() {
  const { id } = useParams();
  const navigate = useNavigate();
  const profile = useQuery({ queryKey: ["patient", id], queryFn: () => api(`/patients/${id}`) });
  if (profile.isLoading) return <Loading />;
  if (profile.error) return <ErrorNote error={profile.error} />;
  const p: Json = profile.data;
  const current = p.consents.filter((c: Json) => c.in_effect);
  return (
    <>
      <button onClick={() => navigate(-1)} className="mb-3 flex items-center gap-1 text-sm text-stone-500 hover:text-stone-800">
        <ArrowLeft className="h-4 w-4" /> Back
      </button>
      <PageHeader
        title={p.name}
        subtitle={`${p.patient_id} · ${p.age} years · ${p.plan_type} · ${p.city}, ${p.state}`}
        action={<SegmentBadge value={p.risk_segment} />}
      />
      <div className="grid gap-5 xl:grid-cols-3">
        <div className="space-y-5 xl:col-span-2">
          {p.therapies.map((t: Json) => (
            <Card
              key={t.therapy_id}
              title={`${titleCase(t.drug_name)} · ${titleCase(t.measure)}`}
              action={<SegmentBadge value={t.risk_segment} />}
            >
              <dl className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-5">
                <Field label="Days covered (PDC)">
                  <span className={cx("tabular font-semibold", t.pdc < 0.8 ? "text-red-700" : "text-emerald-700")}>
                    {pct(t.pdc)}
                  </span>
                </Field>
                <Field label="Days without supply">
                  <span className="tabular">{t.gap_days}</span>
                </Field>
                <Field label="Last fill">{fmtDate(t.last_fill_date)}</Field>
                <Field label="Copay">${t.copay?.toFixed(0)}</Field>
                <Field label="Risk score">
                  <span className="tabular">{t.risk_score?.toFixed(0)} / 100</span>
                </Field>
              </dl>
              <CoverageTimeline fills={p.fills.filter((f: Json) => f.therapy_id === t.therapy_id)} asOf={p.as_of_date} />
            </Card>
          ))}
          <Card title="Outreach history">
            <History items={p.interactions} />
          </Card>
        </div>
        <div className="space-y-5">
          <Card title="Next best action">
            <OpenNba nba={p.open_nba} />
          </Card>
          <Card title="Consent on record">
            <ul className="space-y-1.5 text-sm">
              {current.map((c: Json, i: number) => (
                <li key={i} className="flex items-center justify-between">
                  <span>{c.channel ? `Contact by ${channelName(c.channel).toLowerCase()}` : "Share adherence with provider"}</span>
                  {c.granted ? (
                    <Badge tone="good">
                      <Check className="h-3 w-3" /> Granted
                    </Badge>
                  ) : (
                    <Badge tone="bad">
                      <X className="h-3 w-3" /> Not granted
                    </Badge>
                  )}
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-stone-500">
              Stated preference: {channelName(p.preferred_channel).toLowerCase()}. Only the patient can change consent.
            </p>
          </Card>
          {p.features && (
            <Card title="Response by channel">
              <ChannelTable channels={p.features.channels} />
            </Card>
          )}
          <Card title="Care team">
            <ul className="space-y-1.5 text-sm">
              {p.care_team.map((h: Json) => (
                <li key={h.hcp_id} className="flex items-center justify-between">
                  <span>
                    {h.name} <span className="text-stone-400">· {h.specialty}</span>
                  </span>
                  {h.is_primary && <Badge>Primary</Badge>}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}
