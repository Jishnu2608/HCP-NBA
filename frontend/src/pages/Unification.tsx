// Identity resolution, demonstrated (GET /api/unification): synthetic upstream records matched to
// HCP profiles by fixed rules. Nothing here changes a profile; records that are not a clear
// match are held, never merged.
import { useQuery } from "@tanstack/react-query";
import { Database, GitMerge, ListChecks } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Json } from "../api";
import { Alert, Badge, DataTable, ErrorState, KpiGrid, LoadingRows, PageHeader, Stat, num, pct } from "../ui";
import type { Tone } from "../ui";
import { BentoCard, BentoGrid } from "../layout";

const STATUS_TONE: Record<string, Tone> = { merged: "ok", review: "warn", ambiguous: "bad", unmatched: "neutral" };

export function UnificationPage() {
  const q = useQuery({ queryKey: ["unification"], queryFn: () => api("/unification") });
  if (q.isLoading) return <LoadingRows rows={6} />;
  if (q.error) return <ErrorState error={q.error} retry={() => void q.refetch()} variant="page" title="Unification could not be loaded" />;
  const d: Json = q.data;
  const total = (status: string) => d.sources.reduce((sum: number, s: Json) => sum + s.counts[status], 0);
  return (
    <>
      <PageHeader
        title="Data unification"
        subtitle="How records from upstream systems resolve to one HCP profile. A demonstration on synthetic feeds: profiles are never changed here, and nothing uncertain is merged."
      />
      <Alert tone="info" title="Synthetic sources" className="mb-6">
        The CRM account list and pharmacy-claims prescriber file are generated from the synthetic HCP profiles with the
        imperfections real feeds have (missing NPIs, "LAST, First" names, initials, referral-style records), plus prescribers
        the system does not know. Invited HCPs registered directly and have no upstream records.
      </Alert>
      <KpiGrid>
        <Stat label="Records" value={num(d.records)} hint={`${d.sources.length} sources`} icon={<Database className="h-4 w-4" aria-hidden />} />
        <Stat label="Merged" value={num(total("merged"))} hint={`${d.profiles_with_merged_sources} of ${d.profiles} profiles`} tone="brand" icon={<GitMerge className="h-4 w-4" aria-hidden />} />
        <Stat label="Held or ambiguous" value={num(total("review") + total("ambiguous"))} hint="for a data steward" />
        <Stat label="Unmatched" value={num(total("unmatched"))} hint="no profile in the system" />
      </KpiGrid>
      <BentoGrid>
        <BentoCard span="half" icon={<ListChecks />} title="Matching rules" description="Applied in order; the first rule that finds a profile decides.">
          <ol className="space-y-3 text-sm">
            {d.rules.map((r: Json) => (
              <li key={r.rule} className="flex items-start justify-between gap-3">
                <span>
                  <span className="font-semibold text-ink">{r.rule}</span> <span className="text-ink-muted">{r.label}</span>
                </span>
                <Badge tone={STATUS_TONE[r.status]} className="shrink-0">
                  {d.status_labels[r.status]} · {pct(r.confidence)}
                </Badge>
              </li>
            ))}
            <li className="flex items-start justify-between gap-3">
              <span className="text-ink-muted">Any rule that fits more than one profile</span>
              <Badge tone="bad" className="shrink-0">
                Ambiguous: never merged
              </Badge>
            </li>
          </ol>
        </BentoCard>
        <BentoCard span="half" icon={<Database />} title="By source">
          <ul className="space-y-4">
            {d.sources.map((s: Json) => (
              <li key={s.source}>
                <div className="text-sm font-semibold text-ink">{s.label}</div>
                <div className="mt-1.5 flex flex-wrap gap-2">
                  {Object.entries(s.counts as Record<string, number>).map(([status, n]) => (
                    <Badge key={status} tone={STATUS_TONE[status]}>
                      {d.status_labels[status].split(":")[0]} {n}
                    </Badge>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </BentoCard>
        <BentoCard span="full" icon={<GitMerge />} title="Records not merged" description="What a data steward would look at. Candidate profiles link to HCP 360.">
          <DataTable
            columns={[
              { key: "k", header: "Record", primary: true, cell: (m: Json) => <span className="tabular">{m.source_key}</span> },
              { key: "s", header: "Source", cell: (m: Json) => m.source_label },
              { key: "n", header: "As received", cell: (m: Json) => `${m.name_in_source}${m.state ? `, ${m.state}` : ""}${m.npi_in_source ? ` · NPI ${m.npi_in_source}` : ""}` },
              { key: "st", header: "Result", cell: (m: Json) => <Badge tone={STATUS_TONE[m.status]}>{m.status_label}</Badge> },
              { key: "r", header: "Rule", cell: (m: Json) => m.rule_label ?? "No rule matched" },
              {
                key: "c",
                header: "Candidates",
                cell: (m: Json) =>
                  m.candidates.length ? (
                    <span className="flex flex-wrap gap-x-2">
                      {m.candidates.map((id: string) => (
                        <Link key={id} to={`/hcps/${id}`} className="tabular text-primary-ink underline">
                          {id}
                        </Link>
                      ))}
                    </span>
                  ) : (
                    "—"
                  ),
              },
            ]}
            rows={d.exceptions}
            rowKey={(m) => m.source_key}
            caption="Upstream records that were not merged"
          />
        </BentoCard>
      </BentoGrid>
    </>
  );
}
