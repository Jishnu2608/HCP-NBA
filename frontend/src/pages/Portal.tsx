// Portal pages for the two audiences themselves: an HCP and a patient.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Mail, MessageSquare, Smartphone } from "lucide-react";
import type { ReactNode } from "react";
import { api, post, put } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { P } from "../permissions";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorNote,
  Field,
  Loading,
  PageHeader,
  Table,
  channelName,
  cx,
  fmtDate,
  fmtDateTime,
  pct,
  titleCase,
} from "../ui";

const CHANNEL_ICON: Record<string, ReactNode> = {
  sms: <Smartphone className="h-4 w-4" />,
  email: <Mail className="h-4 w-4" />,
  portal: <MessageSquare className="h-4 w-4" />,
};
const CHANNEL_NAME: Record<string, string> = { sms: "Text message", email: "Email", portal: "Portal message" };

function AdherenceCell({ therapy }: { therapy: Json }) {
  if (therapy.pdc === null) return <span className="text-stone-400">Not yet calculated</span>;
  return therapy.adherent ? (
    <Badge tone="good">On track ({pct(therapy.pdc)} of days covered)</Badge>
  ) : (
    <Badge tone="warn">Below target ({pct(therapy.pdc)} of days covered)</Badge>
  );
}

export function MyMedications() {
  const profile = useQuery({ queryKey: ["me"], queryFn: () => api("/me/profile") });
  if (profile.isLoading) return <Loading />;
  if (profile.error) return <ErrorNote error={profile.error} />;
  const p: Json = profile.data;
  return (
    <>
      <PageHeader
        title={`Hello, ${p.name.split(" ")[0]}`}
        subtitle="Your medications and how consistently you have had them on hand over the last six months."
      />
      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {p.therapies.map((t: Json) => (
            <Card key={t.therapy_id} title={titleCase(t.drug_name)}>
              <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Field label="For">{titleCase(t.measure)}</Field>
                <Field label="Last filled">{fmtDate(t.last_fill_date)}</Field>
                <Field label="Days without supply">
                  <span className={cx("tabular", t.gap_days > 0 && "font-semibold text-red-700")}>
                    {t.gap_days ?? "—"}
                  </span>
                </Field>
                <Field label="Status">
                  <AdherenceCell therapy={t} />
                </Field>
              </dl>
              {t.gap_days > 0 && (
                <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  Your supply appears to have run out. Check your messages, or contact your care team
                  for help with a refill.
                </p>
              )}
            </Card>
          ))}
        </div>
        <Card title="Your care team">
          <ul className="space-y-2 text-sm">
            {p.care_team.map((h: Json) => (
              <li key={h.name}>
                <div className="font-medium text-stone-800">{h.name}</div>
                <div className="text-xs text-stone-500">
                  {h.specialty}
                  {h.is_primary ? " · primary" : ""}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}

export function MyInbox() {
  const { can } = useAuth();
  const client = useQueryClient();
  const inbox = useQuery({ queryKey: ["inbox"], queryFn: () => api<Json[]>("/me/inbox") });
  const respond = useMutation({
    mutationFn: (v: { id: number; response: string }) =>
      post(`/me/inbox/${v.id}/respond`, { response: v.response }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["inbox"] });
      void client.invalidateQueries({ queryKey: ["me"] });
    },
  });
  const isPatient = can(P.SELF_CONSENT_MANAGE);
  if (inbox.isLoading) return <Loading />;
  if (inbox.error) return <ErrorNote error={inbox.error} />;
  return (
    <>
      <PageHeader
        title={isPatient ? "Messages from your care team" : "Inbox"}
        subtitle={
          isPatient
            ? "Messages sent to you by text, email or here in the portal."
            : "Scientific and educational content shared with you. All items are medical-legal-regulatory approved."
        }
      />
      <ErrorNote error={respond.error} />
      <div className="mt-3 space-y-3">
        {!inbox.data!.length && (
          <Card>
            <Empty>No messages yet.</Empty>
          </Card>
        )}
        {inbox.data!.map((m) => {
          const unread = m.status === "pending" || m.status === "no_response";
          return (
            <Card key={m.id} className={cx(unread && "ring-1 ring-brand-500")}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-xs text-stone-500">
                    {CHANNEL_ICON[m.channel]}
                    {CHANNEL_NAME[m.channel]} · {fmtDateTime(m.received)}
                    {unread && <Badge tone="brand">New</Badge>}
                    {m.status === "filled" && (
                      <Badge tone="good">
                        <CheckCircle2 className="h-3 w-3" /> Refill recorded
                      </Badge>
                    )}
                  </div>
                  {m.subject && <h3 className="mt-1 text-sm font-semibold text-stone-900">{m.subject}</h3>}
                  <p className="mt-1 whitespace-pre-line text-sm text-stone-700">{m.body}</p>
                </div>
                <div className="flex shrink-0 flex-col gap-2">
                  {unread && (
                    <Button busy={respond.isPending} onClick={() => respond.mutate({ id: m.id, response: "opened" })}>
                      Mark as read
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
                  {isPatient && m.can_refill && (
                    <Button
                      variant="primary"
                      busy={respond.isPending}
                      onClick={() => respond.mutate({ id: m.id, response: "refill" })}
                    >
                      I have refilled
                    </Button>
                  )}
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}

export function MyConsents() {
  const client = useQueryClient();
  const consents = useQuery({ queryKey: ["consents"], queryFn: () => api<Json[]>("/me/consents") });
  const change = useMutation({
    mutationFn: (v: { channel: string | null; granted: boolean }) =>
      put(v.channel ? `/me/consents/outreach/${v.channel}` : "/me/consents/provider-sharing", {
        granted: v.granted,
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["consents"] }),
  });
  if (consents.isLoading) return <Loading />;
  if (consents.error) return <ErrorNote error={consents.error} />;
  return (
    <>
      <PageHeader
        title="Consent and preferences"
        subtitle="You decide how your care team may contact you. Changes take effect immediately: a channel you switch off cannot be used, even for a message already prepared."
      />
      <ErrorNote error={change.error} />
      <Card className="mt-3 max-w-2xl">
        <ul className="divide-y divide-stone-100">
          {consents.data!.map((c) => (
            <li key={c.channel ?? "sharing"} className="flex items-center justify-between gap-4 py-3">
              <div>
                <div className="text-sm font-medium text-stone-900">
                  {c.channel ? `Contact me by ${channelName(c.channel).toLowerCase()}` : "Share my adherence summary with my doctors"}
                </div>
                <div className="text-xs text-stone-500">
                  {c.effective_from ? `Since ${fmtDate(c.effective_from)}` : "Never set"}
                </div>
              </div>
              <button
                role="switch"
                aria-checked={c.granted}
                disabled={change.isPending}
                onClick={() => change.mutate({ channel: c.channel, granted: !c.granted })}
                className={cx(
                  "relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-60",
                  c.granted ? "bg-brand-600" : "bg-stone-300",
                )}
              >
                <span
                  className={cx(
                    "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all",
                    c.granted ? "left-[22px]" : "left-0.5",
                  )}
                />
              </button>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}

export function MyPatients() {
  const patients = useQuery({ queryKey: ["my-patients"], queryFn: () => api<Json[]>("/me/patients") });
  if (patients.isLoading) return <Loading />;
  if (patients.error) return <ErrorNote error={patients.error} />;
  return (
    <>
      <PageHeader
        title="My patients"
        subtitle="Adherence summary for therapies you prescribed. Only patients who have consented to share with their provider are listed."
      />
      <Card>
        {!patients.data!.length ? (
          <Empty>No patients have consented to sharing.</Empty>
        ) : (
          <Table head={["Patient", "Medication", "Last fill", "Days without supply", "Adherence"]}>
            {patients.data!.flatMap((p) =>
              p.therapies.map((t: Json) => (
                <tr key={`${p.patient_id}-${t.therapy_id}`}>
                  <td className="px-3 py-2.5 font-medium text-stone-800">{p.name}</td>
                  <td className="px-3 py-2.5 text-stone-600">{titleCase(t.drug_name)}</td>
                  <td className="px-3 py-2.5 text-stone-600">{fmtDate(t.last_fill_date)}</td>
                  <td className={cx("tabular px-3 py-2.5", t.gap_days > 0 && "font-semibold text-red-700")}>
                    {t.gap_days}
                  </td>
                  <td className="px-3 py-2.5">
                    <AdherenceCell therapy={t} />
                  </td>
                </tr>
              )),
            )}
          </Table>
        )}
      </Card>
    </>
  );
}

export function MyProfile() {
  const profile = useQuery({ queryKey: ["me"], queryFn: () => api("/me/profile") });
  if (profile.isLoading) return <Loading />;
  if (profile.error) return <ErrorNote error={profile.error} />;
  const p: Json = profile.data;
  return (
    <>
      <PageHeader title={p.name} subtitle="Your profile as held by the engagement team." />
      <Card className="max-w-2xl">
        <dl className="grid grid-cols-2 gap-4">
          <Field label="Specialty">{p.specialty}</Field>
          <Field label="Organisation">{p.organization}</Field>
          <Field label="Location">
            {p.city}, {p.state}
          </Field>
          <Field label="NPI (synthetic)">{p.npi}</Field>
        </dl>
      </Card>
    </>
  );
}
