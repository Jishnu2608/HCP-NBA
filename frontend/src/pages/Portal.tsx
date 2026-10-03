// Portal pages for the two audiences themselves: an HCP and a patient. Plain language,
// own record only.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  HeartPulse,
  Inbox as InboxIcon,
  MapPin,
  Pill,
  ShieldCheck,
  Stethoscope,
  Users,
} from "lucide-react";
import { api, post, put } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { P } from "../permissions";
import { useToast } from "../toast";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  ChannelIcon,
  DataTable,
  EmptyState,
  ErrorNote,
  ErrorState,
  Loading,
  Meter,
  PageHeader,
  Switch,
  channelName,
  cx,
  fmtDate,
  fmtDateTime,
  pct,
  titleCase,
} from "../ui";
import type { Column } from "../ui";

function AdherenceBadge({ therapy }: { therapy: Json }) {
  if (therapy.pdc === null) return <span className="text-sm text-ink-subtle">Not yet calculated</span>;
  return therapy.adherent ? (
    <Badge tone="ok" icon={<CheckCircle2 className="h-3.5 w-3.5" aria-hidden />}>
      On track · {pct(therapy.pdc)} of days covered
    </Badge>
  ) : (
    <Badge tone="warn" icon={<AlertTriangle className="h-3.5 w-3.5" aria-hidden />}>
      Below target · {pct(therapy.pdc)} of days covered
    </Badge>
  );
}

export function MyMedications() {
  const profile = useQuery({ queryKey: ["me"], queryFn: () => api("/me/profile") });
  if (profile.isLoading) return <Loading label="Loading your medications" />;
  if (profile.error) return <ErrorState error={profile.error} title="Your medications could not be loaded" />;
  const p: Json = profile.data;
  const outOfSupply = p.therapies.filter((t: Json) => t.gap_days > 0);
  return (
    <>
      <PageHeader
        title={`Hello, ${p.name.split(" ")[0]}`}
        subtitle="Your medications, and how consistently you have had them on hand recently."
      />

      {outOfSupply.length > 0 ? (
        <Alert tone="warn" title="One of your medications may have run out" className="mb-6">
          Check your messages, or contact your care team for help with a refill.
        </Alert>
      ) : (
        p.therapies.length > 0 && (
          <Alert tone="ok" title="You have every medication on hand" className="mb-6">
            Keep going. Your care team will only contact you if something changes.
          </Alert>
        )
      )}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-4">
          {!p.therapies.length && (
            <Card>
              <EmptyState title="No medications on record" icon={<Pill className="h-5 w-5" />} />
            </Card>
          )}
          {p.therapies.map((t: Json) => (
            <Card
              key={t.therapy_id}
              title={
                <span className="flex items-center gap-2">
                  <Pill className="h-4 w-4 text-ink-subtle" aria-hidden /> {titleCase(t.drug_name)}
                </span>
              }
              description={`For ${t.measure}`}
              action={<AdherenceBadge therapy={t} />}
            >
              {t.pdc !== null && (
                <div className="mb-5">
                  <div className="flex items-baseline justify-between text-[13px] text-ink-subtle">
                    <span>Days with medication on hand</span>
                    <span className="tabular font-semibold text-ink">{pct(t.pdc)}</span>
                  </div>
                  <div className="relative mt-1.5">
                    <Meter value={t.pdc} tone={t.adherent ? "ok" : "warn"} label="Days with medication on hand" />
                    <span className="absolute -top-0.5 h-3 w-0.5 rounded bg-ink" style={{ left: "80%" }} aria-hidden />
                  </div>
                  <div className="mt-1 text-xs text-ink-subtle">The marker shows the 80% goal.</div>
                </div>
              )}
              <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                <div>
                  <dt className="text-[13px] text-ink-subtle">Last filled</dt>
                  <dd className="tabular mt-0.5 text-sm font-semibold text-ink">{fmtDate(t.last_fill_date)}</dd>
                </div>
                <div>
                  <dt className="text-[13px] text-ink-subtle">Days without supply</dt>
                  <dd className={cx("tabular mt-0.5 text-sm font-semibold", t.gap_days > 0 ? "text-bad" : "text-ink")}>
                    {t.gap_days ?? "—"}
                  </dd>
                </div>
                <div>
                  <dt className="text-[13px] text-ink-subtle">Supply per fill</dt>
                  <dd className="tabular mt-0.5 text-sm font-semibold text-ink">{t.days_supply} days</dd>
                </div>
              </dl>
            </Card>
          ))}
        </div>
        <Card title="Your care team">
          {p.care_team.length ? (
            <ul className="space-y-3">
              {p.care_team.map((h: Json) => (
                <li key={h.name} className="flex items-center gap-3">
                  <Avatar name={h.name} size="sm" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold text-ink">{h.name}</div>
                    <div className="truncate text-[13px] text-ink-subtle">{h.specialty}</div>
                  </div>
                  {h.is_primary && (
                    <Badge tone="sage" icon={<HeartPulse className="h-3.5 w-3.5" aria-hidden />}>
                      Primary
                    </Badge>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ink-subtle">No care team on record.</p>
          )}
        </Card>
      </div>
    </>
  );
}

export function MyInbox() {
  const { can } = useAuth();
  const client = useQueryClient();
  const toast = useToast();
  const inbox = useQuery({ queryKey: ["inbox"], queryFn: () => api<Json[]>("/me/inbox") });
  const respond = useMutation({
    mutationFn: (v: { id: number; response: string }) => post(`/me/inbox/${v.id}/respond`, { response: v.response }),
    onSuccess: (_, v) => {
      if (v.response === "refill") toast("Thank you. Your refill is recorded.");
      void client.invalidateQueries({ queryKey: ["inbox"] });
      void client.invalidateQueries({ queryKey: ["me"] });
    },
  });
  const isPatient = can(P.SELF_CONSENT_MANAGE);
  if (inbox.isLoading) return <Loading label="Loading messages" rows={4} />;
  if (inbox.error) return <ErrorState error={inbox.error} title="Messages could not be loaded" />;
  const unreadCount = inbox.data!.filter((m) => m.status === "pending" || m.status === "no_response").length;
  return (
    <>
      <PageHeader
        title={isPatient ? "Messages from your care team" : "Inbox"}
        meta={unreadCount > 0 && <Badge tone="accent">{unreadCount} new</Badge>}
        subtitle={
          isPatient
            ? "Messages sent to you by text, email or here in the portal."
            : "Scientific and educational content shared with you. Every item is medical-legal-regulatory approved."
        }
      />
      <ErrorNote error={respond.error} className="mb-4" />
      {!inbox.data!.length ? (
        <Card>
          <EmptyState title="No messages yet" icon={<InboxIcon className="h-5 w-5" />}>
            {isPatient ? "Messages from your care team will appear here." : "Content shared with you will appear here."}
          </EmptyState>
        </Card>
      ) : (
        <ul className="space-y-3">
          {inbox.data!.map((m) => {
            const unread = m.status === "pending" || m.status === "no_response";
            return (
              <li
                key={m.id}
                className={cx(
                  "relative rounded-xl border bg-surface p-5 shadow-card sm:p-6",
                  unread ? "border-primary-line" : "border-line",
                )}
              >
                {unread && <span className="absolute left-0 top-5 h-8 w-1 rounded-r-full bg-accent" aria-hidden />}
                <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-ink-subtle">
                      <ChannelIcon channel={m.channel} className="h-4 w-4" />
                      <span>{channelName(m.channel)}</span>
                      <span aria-hidden>·</span>
                      <span className="tabular">{fmtDateTime(m.received)}</span>
                      {unread && <Badge tone="accent">New</Badge>}
                      {m.status === "filled" && (
                        <Badge tone="ok" icon={<CheckCircle2 className="h-3.5 w-3.5" aria-hidden />}>
                          Refill recorded
                        </Badge>
                      )}
                    </div>
                    {m.subject && <h2 className="mt-2 text-[15px] font-semibold text-ink">{m.subject}</h2>}
                    <p className="mt-1.5 max-w-3xl whitespace-pre-line text-sm leading-6 text-ink-muted">{m.body}</p>
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2 md:flex-col md:items-stretch">
                    {isPatient && m.can_refill && (
                      <Button
                        variant="primary"
                        busy={respond.isPending}
                        onClick={() => respond.mutate({ id: m.id, response: "refill" })}
                      >
                        I have refilled
                      </Button>
                    )}
                    {!isPatient && m.status !== "clicked" && (
                      <Button
                        variant="primary"
                        busy={respond.isPending}
                        onClick={() => respond.mutate({ id: m.id, response: "clicked" })}
                      >
                        View full content
                      </Button>
                    )}
                    {unread && (
                      <Button busy={respond.isPending} onClick={() => respond.mutate({ id: m.id, response: "opened" })}>
                        Mark as read
                      </Button>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

export function MyConsents() {
  const client = useQueryClient();
  const toast = useToast();
  const consents = useQuery({ queryKey: ["consents"], queryFn: () => api<Json[]>("/me/consents") });
  const change = useMutation({
    mutationFn: (v: { channel: string | null; granted: boolean }) =>
      put(v.channel ? `/me/consents/outreach/${v.channel}` : "/me/consents/provider-sharing", {
        granted: v.granted,
      }),
    onSuccess: (_, v) => {
      toast(v.granted ? "Preference saved: allowed" : "Preference saved: not allowed");
      void client.invalidateQueries({ queryKey: ["consents"] });
    },
  });
  if (consents.isLoading) return <Loading label="Loading your preferences" rows={4} />;
  if (consents.error) return <ErrorState error={consents.error} title="Your preferences could not be loaded" />;
  const outreach = consents.data!.filter((c) => c.channel);
  const sharing = consents.data!.filter((c) => !c.channel);

  const Row = ({ c }: { c: Json }) => {
    const label = c.channel
      ? `Contact me by ${channelName(c.channel).toLowerCase()}`
      : "Share my adherence summary with my doctors";
    return (
      <li className="flex items-center justify-between gap-4 py-4 first:pt-0 last:pb-0">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-subtle text-ink-muted">
            {c.channel ? <ChannelIcon channel={c.channel} /> : <Stethoscope className="h-4 w-4" aria-hidden />}
          </span>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-ink">{label}</div>
            <div className="mt-0.5 text-[13px] text-ink-subtle">
              {c.granted ? "Allowed" : "Not allowed"} · {c.effective_from ? `since ${fmtDate(c.effective_from)}` : "never set"}
            </div>
          </div>
        </div>
        <Switch
          checked={c.granted}
          label={label}
          disabled={change.isPending}
          onChange={(granted) => change.mutate({ channel: c.channel, granted })}
        />
      </li>
    );
  };

  return (
    <>
      <PageHeader
        title="Consent and preferences"
        subtitle="You decide how your care team may contact you. Changes take effect immediately: a channel you switch off cannot be used, even for a message already prepared."
      />
      <ErrorNote error={change.error} className="mb-4" />
      <div className="grid max-w-3xl gap-6">
        <Card title="How we may contact you" description="Applies to reminders and messages from your care team.">
          <ul className="divide-y divide-line">
            {outreach.map((c) => (
              <Row key={c.channel} c={c} />
            ))}
          </ul>
        </Card>
        {sharing.length > 0 && (
          <Card title="Sharing with your doctors">
            <ul className="divide-y divide-line">
              {sharing.map((c) => (
                <Row key="sharing" c={c} />
              ))}
            </ul>
          </Card>
        )}
        <p className="flex items-start gap-2 text-[13px] text-ink-subtle">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          Every change is recorded with the date. Your care team cannot change these settings for you.
        </p>
      </div>
    </>
  );
}

export function MyPatients() {
  const patients = useQuery({ queryKey: ["my-patients"], queryFn: () => api<Json[]>("/me/patients") });
  if (patients.isLoading) return <Loading label="Loading your patients" />;
  if (patients.error) return <ErrorState error={patients.error} title="Your patients could not be loaded" />;
  const rows = patients.data!.flatMap((p) => p.therapies.map((t: Json) => ({ ...t, name: p.name, patient_id: p.patient_id })));
  const columns: Column<Json>[] = [
    {
      key: "patient",
      header: "Patient",
      primary: true,
      cell: (r) => (
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={r.name} size="sm" />
          <span className="truncate font-semibold text-ink">{r.name}</span>
        </div>
      ),
    },
    { key: "drug", header: "Medication", cell: (r) => <span className="text-ink-muted">{titleCase(r.drug_name)}</span> },
    { key: "fill", header: "Last refill", cell: (r) => <span className="tabular text-ink-muted">{fmtDate(r.last_fill_date)}</span> },
    {
      key: "gap",
      header: "Days without supply",
      align: "right",
      cell: (r) => <span className={cx("tabular font-semibold", r.gap_days > 0 ? "text-bad" : "text-ink")}>{r.gap_days}</span>,
    },
    { key: "adherence", header: "Adherence", hideOnMobile: true, cell: (r) => <AdherenceBadge therapy={r} /> },
  ];
  return (
    <>
      <PageHeader
        title="My patients"
        subtitle="Adherence summary for therapies you prescribed. Only patients who have agreed to share with their provider are listed."
      />
      <Card flush>
        {!rows.length ? (
          <EmptyState title="No patients have agreed to sharing" icon={<Users className="h-5 w-5" />}>
            Patients appear here once they allow their adherence summary to be shared with you.
          </EmptyState>
        ) : (
          <DataTable
            caption="My patients"
            columns={columns}
            rows={rows}
            rowKey={(r) => `${r.patient_id}-${r.therapy_id}`}
            mobileAside={(r) => (r.adherent ? <Badge tone="ok">On track</Badge> : <Badge tone="warn">Below target</Badge>)}
          />
        )}
      </Card>
    </>
  );
}

export function MyProfile() {
  const profile = useQuery({ queryKey: ["me"], queryFn: () => api("/me/profile") });
  if (profile.isLoading) return <Loading label="Loading your profile" rows={3} />;
  if (profile.error) return <ErrorState error={profile.error} title="Your profile could not be loaded" />;
  const p: Json = profile.data;
  return (
    <>
      <PageHeader title="Profile" subtitle="Your professional profile as held by the engagement team." />
      <Card className="max-w-3xl">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
          <Avatar name={p.name} size="lg" />
          <div className="min-w-0">
            <div className="text-xl font-semibold text-ink">{p.name}</div>
            <div className="mt-0.5 text-sm text-ink-muted">{p.specialty}</div>
          </div>
        </div>
        <dl className="mt-6 grid gap-4 border-t border-line pt-5 sm:grid-cols-2">
          <div className="flex gap-3">
            <Building2 className="mt-0.5 h-4 w-4 shrink-0 text-ink-subtle" aria-hidden />
            <div className="min-w-0">
              <dt className="text-[13px] text-ink-subtle">Organization</dt>
              <dd className="break-words text-sm font-semibold text-ink">{p.organization}</dd>
            </div>
          </div>
          <div className="flex gap-3">
            <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-ink-subtle" aria-hidden />
            <div>
              <dt className="text-[13px] text-ink-subtle">Location</dt>
              <dd className="text-sm font-semibold text-ink">
                {p.city}, {p.state}
              </dd>
            </div>
          </div>
          <div className="flex gap-3">
            <Stethoscope className="mt-0.5 h-4 w-4 shrink-0 text-ink-subtle" aria-hidden />
            <div>
              <dt className="text-[13px] text-ink-subtle">Specialty</dt>
              <dd className="text-sm font-semibold text-ink">{p.specialty}</dd>
            </div>
          </div>
          <div className="flex gap-3">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-ink-subtle" aria-hidden />
            <div>
              <dt className="text-[13px] text-ink-subtle">NPI (synthetic)</dt>
              <dd className="tabular text-sm font-semibold text-ink">{p.npi}</dd>
            </div>
          </div>
        </dl>
      </Card>
    </>
  );
}
