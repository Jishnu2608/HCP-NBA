import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, query } from "../api";
import type { Json } from "../api";
import {
  Card,
  Empty,
  ErrorNote,
  Field,
  Loading,
  PageHeader,
  SegmentBadge,
  Table,
  channelName,
  pct,
  titleCase,
} from "../ui";
import { ChannelTable, History, OpenNba } from "./Patients";

export function HcpList() {
  const [q, setQ] = useState("");
  const list = useQuery({
    queryKey: ["hcps", q],
    queryFn: () => api(`/hcps${query({ q, limit: 100 })}`),
  });
  return (
    <>
      <PageHeader
        title="Healthcare professionals"
        subtitle="Segments combine prescribing volume with how often each HCP engages. Highest value first."
        action={
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search name, ID or specialty"
            className="w-64 rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-sm"
          />
        }
      />
      <Card>
        {list.isLoading ? (
          <Loading />
        ) : list.error ? (
          <ErrorNote error={list.error} />
        ) : !list.data.items.length ? (
          <Empty>No HCPs match.</Empty>
        ) : (
          <Table head={["HCP", "Specialty", "Organisation", "Segment", "Value score", "Best channel"]}>
            {list.data.items.map((h: Json) => (
              <tr key={h.hcp_id} className="hover:bg-stone-50">
                <td className="px-3 py-2.5">
                  <Link to={`/hcps/${h.hcp_id}`} className="font-medium text-brand-700 hover:underline">
                    {h.name}
                  </Link>
                  <span className="ml-2 text-xs text-stone-400">{h.hcp_id}</span>
                </td>
                <td className="px-3 py-2.5 text-stone-600">{h.specialty}</td>
                <td className="px-3 py-2.5 text-stone-600">
                  {h.organization}, {h.state}
                </td>
                <td className="px-3 py-2.5">
                  <SegmentBadge value={h.segment} />
                </td>
                <td className="tabular px-3 py-2.5">{h.value_score?.toFixed(0)}</td>
                <td className="px-3 py-2.5 text-stone-600">{channelName(h.channel_affinity)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}

function RateList({ title, rates }: { title: string; rates: Record<string, number> }) {
  const entries = Object.entries(rates).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return null;
  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-stone-500">{title}</div>
      <ul className="space-y-1.5">
        {entries.map(([name, rate]) => (
          <li key={name} className="flex items-center gap-3 text-sm">
            <span className="w-28 shrink-0 text-stone-700">{titleCase(name)}</span>
            <span className="h-2 flex-1 overflow-hidden rounded-full bg-stone-100">
              <span className="block h-full rounded-full bg-brand-500" style={{ width: `${rate * 100}%` }} />
            </span>
            <span className="tabular w-10 text-right text-stone-600">{pct(rate)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function HcpProfile() {
  const { id } = useParams();
  const navigate = useNavigate();
  const profile = useQuery({ queryKey: ["hcp", id], queryFn: () => api(`/hcps/${id}`) });
  if (profile.isLoading) return <Loading />;
  if (profile.error) return <ErrorNote error={profile.error} />;
  const h: Json = profile.data;
  return (
    <>
      <button onClick={() => navigate(-1)} className="mb-3 flex items-center gap-1 text-sm text-stone-500 hover:text-stone-800">
        <ArrowLeft className="h-4 w-4" /> Back
      </button>
      <PageHeader
        title={h.name}
        subtitle={`${h.hcp_id} · ${h.specialty} · ${h.organization}, ${h.city}, ${h.state}`}
        action={<SegmentBadge value={h.segment} />}
      />
      <div className="grid gap-5 xl:grid-cols-3">
        <div className="space-y-5 xl:col-span-2">
          <Card title="Profile">
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Field label="Value score">
                <span className="tabular font-semibold">{h.value_score?.toFixed(0)} / 100</span>
              </Field>
              <Field label="Prescriptions / year">
                <span className="tabular">{h.rx_volume_annual.toLocaleString()}</span>
              </Field>
              <Field label="Best channel">{channelName(h.channel_affinity)}</Field>
              <Field label="NPI (synthetic)">{h.npi}</Field>
            </dl>
          </Card>
          {h.features && (
            <Card title="What this HCP engages with">
              <div className="grid gap-6 md:grid-cols-2">
                <RateList title="By therapy area" rates={h.features.topic_rates.measure ?? {}} />
                <RateList title="By content type" rates={h.features.topic_rates.subtopic ?? {}} />
              </div>
              <p className="mt-3 text-xs text-stone-500">
                Engagement rates are smoothed toward the average, so one response is not over-read.
              </p>
            </Card>
          )}
          <Card title="Interaction history">
            <History items={h.interactions} />
          </Card>
        </div>
        <div className="space-y-5">
          <Card title="Next best action">
            <OpenNba nba={h.open_nba} />
          </Card>
          {h.features && (
            <Card title="Response by channel">
              <ChannelTable channels={h.features.channels} />
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
