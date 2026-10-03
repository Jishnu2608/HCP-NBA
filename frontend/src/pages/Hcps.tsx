import { useQuery } from "@tanstack/react-query";
import { Award, Building2, FileText, MapPin, MessagesSquare, Stethoscope } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, query } from "../api";
import type { Json } from "../api";
import {
  Avatar,
  Card,
  ChannelIcon,
  DataTable,
  EmptyState,
  ErrorState,
  Loading,
  LoadingRows,
  Meter,
  PageHeader,
  SearchInput,
  SegmentBadge,
  KpiGrid,
  Stat,
  Toolbar,
  channelName,
  num,
  pct,
  titleCase,
} from "../ui";
import type { Column } from "../ui";
import { ChannelTable, History, OpenNba } from "./Patients";

export function HcpList() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const list = useQuery({
    queryKey: ["hcps", q],
    queryFn: () => api(`/hcps${query({ q, limit: 100 })}`),
    placeholderData: (previous) => previous,
  });

  const columns: Column<Json>[] = [
    {
      key: "hcp",
      header: "HCP",
      primary: true,
      className: "min-w-56",
      cell: (h) => (
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={h.name} size="sm" />
          <div className="min-w-0">
            <Link
              to={`/hcps/${h.hcp_id}`}
              tabIndex={-1}
              onClick={(e) => e.stopPropagation()}
              className="block font-semibold text-ink [overflow-wrap:anywhere] hover:text-primary-ink hover:underline"
              title={h.name}
            >
              {h.name}
            </Link>
            <div className="tabular text-xs text-ink-subtle">{h.hcp_id}</div>
          </div>
        </div>
      ),
    },
    { key: "specialty", header: "Specialty", cell: (h) => <span className="text-ink-muted">{h.specialty}</span> },
    {
      key: "org",
      header: "Organization",
      className: "max-w-64",
      cell: (h) => (
        <span className="block truncate text-ink-muted" title={`${h.organization}, ${h.state}`}>
          {h.organization}, {h.state}
        </span>
      ),
    },
    { key: "segment", header: "Segment", hideOnMobile: true, cell: (h) => <SegmentBadge value={h.segment} /> },
    {
      key: "value",
      header: "Value score",
      align: "right",
      cell: (h) => (
        <div className="ml-auto flex w-28 items-center gap-2">
          <Meter value={(h.value_score ?? 0) / 100} label="Value score" className="flex-1" />
          <span className="tabular w-7 text-right font-semibold text-ink">{h.value_score?.toFixed(0)}</span>
        </div>
      ),
    },
    {
      key: "channel",
      header: "Best channel",
      cell: (h) => (
        <span className="inline-flex items-center gap-1.5 text-ink-muted">
          <ChannelIcon channel={h.channel_affinity} className="h-3.5 w-3.5" />
          {channelName(h.channel_affinity)}
        </span>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Healthcare professionals"
        subtitle="Segments combine prescribing volume with how often each HCP engages. Highest value first."
      />
      <Toolbar>
        <p className="text-sm text-ink-subtle">
          {list.data ? `${num(list.data.total)} HCPs` : " "}
        </p>
        <SearchInput
          label="Search HCPs"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name, ID or specialty"
          className="w-full lg:w-80"
        />
      </Toolbar>
      <Card flush>
        {list.isLoading ? (
          <div className="p-5">
            <LoadingRows rows={8} label="Loading HCPs" />
          </div>
        ) : list.error ? (
          <ErrorState error={list.error} retry={() => void list.refetch()} title="HCPs could not be loaded" />
        ) : !list.data.items.length ? (
          <EmptyState title={q ? "No HCPs match your search" : "No HCPs assigned"} icon={<Stethoscope className="h-5 w-5" />}>
            {q ? "Try a last name, an HCP ID or a specialty." : "An administrator can assign HCPs to your account."}
          </EmptyState>
        ) : (
          <DataTable
            caption="Healthcare professionals"
            columns={columns}
            rows={list.data.items}
            rowKey={(h) => h.hcp_id}
            onRowClick={(h) => navigate(`/hcps/${h.hcp_id}`)}
            mobileAside={(h) => <SegmentBadge value={h.segment} />}
          />
        )}
      </Card>
    </>
  );
}

function RateList({ title, rates }: { title: string; rates: Record<string, number> }) {
  const entries = Object.entries(rates).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return null;
  return (
    <div className="min-w-0">
      <h3 className="mb-3 text-[13px] font-semibold text-ink-muted">{title}</h3>
      <ul className="space-y-3">
        {entries.map(([name, rate]) => (
          <li key={name}>
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="truncate text-ink">{titleCase(name)}</span>
              <span className="tabular shrink-0 font-semibold text-ink">{pct(rate)}</span>
            </div>
            <Meter value={rate} className="mt-1.5" label={`${titleCase(name)} engagement`} />
          </li>
        ))}
      </ul>
    </div>
  );
}

export function HcpProfile() {
  const { id } = useParams();
  const profile = useQuery({ queryKey: ["hcp", id], queryFn: () => api(`/hcps/${id}`) });
  if (profile.isLoading) return <Loading label="Loading HCP" />;
  if (profile.error) return <ErrorState error={profile.error} retry={() => void profile.refetch()} variant="page" title="This HCP could not be loaded" />;
  const h: Json = profile.data;
  const touches = h.interactions.length;
  const engaged = h.interactions.filter((i: Json) => !["no_response", "declined", "pending"].includes(i.outcome)).length;
  return (
    <>
      <PageHeader
        back
        title={
          <span className="flex items-center gap-3">
            <Avatar name={h.name} size="lg" />
            <span className="min-w-0 break-words">{h.name}</span>
          </span>
        }
        meta={<SegmentBadge value={h.segment} />}
        subtitle={
          <span className="flex flex-wrap gap-x-4 gap-y-1">
            <span className="inline-flex items-center gap-1.5">
              <Stethoscope className="h-4 w-4 text-ink-subtle" aria-hidden /> {h.specialty}
            </span>
            <span className="inline-flex min-w-0 items-center gap-1.5">
              <Building2 className="h-4 w-4 shrink-0 text-ink-subtle" aria-hidden /> <span className="break-words">{h.organization}</span>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <MapPin className="h-4 w-4 text-ink-subtle" aria-hidden /> {h.city}, {h.state}
            </span>
          </span>
        }
      />

      <KpiGrid>
        <Stat label="Value score" value={`${h.value_score?.toFixed(0) ?? "—"} / 100`} hint="prescribing volume and engagement" icon={<Award className="h-4 w-4" aria-hidden />} tone="brand" />
        <Stat label="Prescriptions per year" value={num(h.rx_volume_annual)} hint={`NPI ${h.npi} (synthetic)`} icon={<FileText className="h-4 w-4" aria-hidden />} />
        <Stat
          label="Best channel"
          value={<span className="text-lg">{channelName(h.channel_affinity)}</span>}
          icon={<ChannelIcon channel={h.channel_affinity} />}
        />
        <Stat
          label="Interactions"
          value={num(touches)}
          hint={touches ? `${engaged} with a response` : "none on record"}
          icon={<MessagesSquare className="h-4 w-4" aria-hidden />}
        />
      </KpiGrid>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_360px] xl:grid-rows-[auto_1fr]">
        <div className="min-w-0 xl:col-start-2 xl:row-start-1">
          <OpenNba nba={h.open_nba} />
        </div>
        <div className="min-w-0 space-y-6 xl:col-start-1 xl:row-span-2 xl:row-start-1">
          {h.features && (
            <Card title="What this HCP engages with" description="Smoothed toward the average, so one response is not over-read.">
              <div className="grid gap-8 md:grid-cols-2">
                <RateList title="By therapy area" rates={h.features.topic_rates.measure ?? {}} />
                <RateList title="By content type" rates={h.features.topic_rates.subtopic ?? {}} />
              </div>
            </Card>
          )}
          <Card title="Engagement timeline" description="Newest first. Engine recommendations are highlighted.">
            <History items={h.interactions} />
          </Card>
        </div>
        <div className="min-w-0 space-y-6 xl:col-start-2 xl:row-start-2">
          {h.features && (
            <Card title="Response by channel">
              <ChannelTable channels={h.features.channels} />
            </Card>
          )}
          <Card title="Profile">
            <dl className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <dt className="text-[13px] text-ink-subtle">HCP ID</dt>
                <dd className="tabular font-medium text-ink">{h.hcp_id}</dd>
              </div>
              <div>
                <dt className="text-[13px] text-ink-subtle">NPI (synthetic)</dt>
                <dd className="tabular font-medium text-ink">{h.npi}</dd>
              </div>
              <div>
                <dt className="text-[13px] text-ink-subtle">Taxonomy</dt>
                <dd className="tabular font-medium text-ink">{h.taxonomy_code ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-[13px] text-ink-subtle">Segment</dt>
                <dd className="font-medium text-ink">{h.segment ? titleCase(h.segment) : "—"}</dd>
              </div>
            </dl>
          </Card>
        </div>
      </div>
    </>
  );
}
