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
  Plus,
  RefreshCcw,
  ShieldCheck,
  Stethoscope,
  Users,
} from "lucide-react";
import { useCallback, useState } from "react";
import { api, post, put } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { P } from "../permissions";
import { preferences } from "../session";
import { useToast } from "../toast";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  ChannelIcon,
  DataTable,
  Drawer,
  EmptyState,
  ErrorNote,
  ErrorState,
  Loading,
  Meter,
  PageHeader,
  PersonName,
  Segmented,
  Switch,
  TextArea,
  channelName,
  cx,
  fmtDate,
  fmtDateTime,
  pct,
  titleCase,
} from "../ui";
import type { Column } from "../ui";
import {
  CareTeam,
  ConditionForm,
  ConditionList,
  ConsultForm,
  MedicationForm,
  NoteList,
  RequestList,
  SpecialtyChips,
  SpecialtyPicker,
  StatusChip,
  measureLabel,
  specialtyText,
} from "./HealthForms";

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

function MedicationCard({ m, onRefill, refilling }: { m: Json; onRefill?: () => void; refilling?: boolean }) {
  const confirmed = m.review_status === "confirmed";
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Pill className="h-4 w-4 text-ink-subtle" aria-hidden /> {titleCase(m.drug_name)}
        </span>
      }
      description={m.measure ? `For ${measureLabel(m.measure).toLowerCase()}` : undefined}
      action={confirmed ? <AdherenceBadge therapy={m} /> : <StatusChip status={m.review_status} />}
    >
      {(m.dose_instructions || m.schedule) && (
        <p className="mb-4 text-sm text-ink-muted">{[m.dose_instructions, m.schedule].filter(Boolean).join(" · ")}</p>
      )}
      {confirmed && m.pdc != null && (
        <div className="mb-5">
          <div className="flex items-baseline justify-between text-[13px] text-ink-subtle">
            <span>Days with medication on hand</span>
            <span className="tabular font-semibold text-ink">{pct(m.pdc)}</span>
          </div>
          <div className="relative mt-1.5">
            <Meter value={m.pdc} tone={m.adherent ? "ok" : "warn"} label="Days with medication on hand" />
            <span className="absolute -top-0.5 h-3 w-0.5 rounded bg-ink" style={{ left: "80%" }} aria-hidden />
          </div>
          <div className="mt-1 text-xs text-ink-subtle">The marker shows the 80% goal.</div>
        </div>
      )}
      {confirmed ? (
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <div>
            <dt className="text-[13px] text-ink-subtle">Last filled</dt>
            <dd className="tabular mt-0.5 text-sm font-semibold text-ink">{fmtDate(m.last_fill_date)}</dd>
          </div>
          <div>
            <dt className="text-[13px] text-ink-subtle">Days without supply</dt>
            <dd className={cx("tabular mt-0.5 text-sm font-semibold", m.gap_days > 0 ? "text-bad" : "text-ink")}>
              {m.gap_days ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-[13px] text-ink-subtle">Supply per fill</dt>
            <dd className="tabular mt-0.5 text-sm font-semibold text-ink">{m.days_supply} days</dd>
          </div>
        </dl>
      ) : (
        <p className="text-sm text-ink-subtle">
          {m.review_status === "reported"
            ? "Your care team will review this medication and add the supply details."
            : `Stopped${m.end_date ? ` on ${fmtDate(m.end_date)}` : ""}.`}
        </p>
      )}
      {onRefill && confirmed && (
        <div className="mt-4 border-t border-line pt-4">
          <Button size="sm" busy={refilling} onClick={onRefill}>
            <RefreshCcw className="h-3.5 w-3.5" aria-hidden /> I refilled today
          </Button>
        </div>
      )}
    </Card>
  );
}

type Panel = "condition" | "medication" | "consult" | null;

/** The patient's own health profile: what they reported, what their care team confirmed,
 *  who looks after them, and a way to ask for a consultation. */
export function MyMedications() {
  const { user, can } = useAuth();
  const client = useQueryClient();
  const toast = useToast();
  const profile = useQuery({ queryKey: ["me"], queryFn: () => api("/me/profile") });
  const health = useQuery({
    queryKey: ["me", "health"],
    queryFn: () => api("/me/health"),
    enabled: can(P.SELF_HEALTH_MANAGE),
  });
  const [panel, setPanel] = useState<Panel>(null);
  const close = useCallback(() => setPanel(null), []);
  const [skipped, setSkipped] = useState(() => (user ? preferences.profileSkipped(user.id) : false));
  const saved = (message: string) => (data: Json) => {
    client.setQueryData(["me", "health"], data);
    void client.invalidateQueries({ queryKey: ["me"] });
    setPanel(null);
    toast(message);
  };
  const addCondition = useMutation({
    mutationFn: (body: Json) => post("/me/conditions", body),
    onSuccess: saved("Condition added. Your care team will review it."),
  });
  const addMedication = useMutation({
    mutationFn: (body: Json) => post("/me/medications", body),
    onSuccess: saved("Medication added. Your care team will review it."),
  });
  const consult = useMutation({
    mutationFn: (reason: string) => post("/me/care-requests", { reason }),
    onSuccess: saved("Request sent to your care manager."),
  });
  const refill = useMutation({
    mutationFn: (id: number) => post(`/me/medications/${id}/refill`, {}),
    onSuccess: saved("Refill recorded."),
  });

  if (profile.isLoading || health.isLoading) return <Loading label="Loading your health profile" />;
  const failed = profile.error ?? health.error;
  if (failed)
    return (
      <ErrorState
        error={failed}
        retry={() => void Promise.all([profile.refetch(), health.refetch()])}
        variant="page"
        title="Your health profile could not be loaded"
      />
    );
  const p: Json = profile.data;
  const h: Json = health.data ?? { conditions: [], medications: [], requests: [], notes: [], care_team: { hcps: [], care_managers: [] }, editable: false };
  const editable: boolean = h.editable;
  const empty = !h.conditions.length && !h.medications.length;
  const outOfSupply = h.medications.filter((m: Json) => m.review_status === "confirmed" && m.gap_days > 0);
  const openRequests = h.requests.filter((r: Json) => r.status !== "closed").length;

  return (
    <>
      <PageHeader
        title={`Hello, ${p.name.split(" ")[0]}`}
        subtitle="Your conditions and medications, and the people looking after you."
        meta={
          p.location && (
            <Badge tone="neutral" icon={<MapPin className="h-3.5 w-3.5" aria-hidden />}>
              {p.location}
            </Badge>
          )
        }
        action={
          editable && (
            <Button variant="primary" onClick={() => setPanel("consult")}>
              <Stethoscope className="h-4 w-4" aria-hidden /> Consult a HCP
            </Button>
          )
        }
      />

      {editable && empty && !skipped && (
        <section
          aria-labelledby="builder-title"
          className="mb-6 rounded-xl border border-primary-line bg-primary-soft/50 p-5 sm:p-6"
        >
          <h2 id="builder-title" className="text-[17px] font-semibold text-ink">
            Let's build your health profile
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-ink-muted">
            Your profile starts empty. Add what you know; your care manager reviews it and connects you with the right
            healthcare professional. Nothing is added for you.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="primary" onClick={() => setPanel("condition")}>
              <HeartPulse className="h-4 w-4" aria-hidden /> Add an illness or condition
            </Button>
            <Button onClick={() => setPanel("medication")}>
              <Pill className="h-4 w-4" aria-hidden /> Add a medication
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                if (user) preferences.setProfileSkipped(user.id);
                setSkipped(true);
              }}
            >
              Skip for now
            </Button>
          </div>
        </section>
      )}

      {outOfSupply.length > 0 ? (
        <Alert tone="warn" title="One of your medications may have run out" className="mb-6">
          Check your messages, or contact your care team for help with a refill.
        </Alert>
      ) : (
        h.medications.some((m: Json) => m.review_status === "confirmed" && m.pdc != null) && (
          <Alert tone="ok" title="You have every medication on hand" className="mb-6">
            Keep going. Your care team will only contact you if something changes.
          </Alert>
        )
      )}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-6">
          <Card
            title="Conditions"
            action={
              editable && (
                <Button size="sm" onClick={() => setPanel("condition")}>
                  <Plus className="h-3.5 w-3.5" aria-hidden /> Add
                </Button>
              )
            }
          >
            <ConditionList items={h.conditions} />
          </Card>
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-[15px] font-semibold text-ink">Medications</h2>
              {editable && (
                <Button size="sm" onClick={() => setPanel("medication")}>
                  <Plus className="h-3.5 w-3.5" aria-hidden /> Add medication
                </Button>
              )}
            </div>
            <ErrorNote error={refill.error} />
            {!h.medications.length && (
              <Card>
                <EmptyState title="No medications on record" icon={<Pill className="h-5 w-5" />} compact>
                  {editable ? "Add a medication you take; your care team confirms it." : undefined}
                </EmptyState>
              </Card>
            )}
            {h.medications.map((m: Json) => (
              <MedicationCard
                key={m.therapy_id}
                m={m}
                onRefill={editable ? () => refill.mutate(m.therapy_id) : undefined}
                refilling={refill.isPending && refill.variables === m.therapy_id}
              />
            ))}
          </div>
        </div>
        <div className="min-w-0 space-y-6">
          <Card title="Your care team">
            <CareTeam team={h.care_team} />
          </Card>
          <Card
            title="Instructions from your care team"
            description="What your healthcare professional asked you to do, as recorded by your care team."
          >
            <NoteList items={h.notes} />
          </Card>
          <Card
            title="Your requests"
            action={openRequests > 0 && <Badge tone="warn">{openRequests} open</Badge>}
          >
            <RequestList items={h.requests} />
          </Card>
        </div>
      </div>

      {panel && (
        <Drawer
          title={
            panel === "condition" ? "Add a condition" : panel === "medication" ? "Add a medication" : "Consult a HCP"
          }
          onClose={close}
        >
          {panel === "condition" && (
            <ConditionForm busy={addCondition.isPending} error={addCondition.error} onSubmit={(b) => addCondition.mutate(b)} />
          )}
          {panel === "medication" && (
            <MedicationForm
              busy={addMedication.isPending}
              error={addMedication.error}
              onSubmit={(b) => addMedication.mutate(b)}
            />
          )}
          {panel === "consult" && (
            <ConsultForm busy={consult.isPending} error={consult.error} onSubmit={(r) => consult.mutate(r)} />
          )}
        </Drawer>
      )}
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
  if (inbox.error) return <ErrorState error={inbox.error} retry={() => void inbox.refetch()} variant="page" title="Messages could not be loaded" />;
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
  if (consents.error) return <ErrorState error={consents.error} retry={() => void consents.refetch()} variant="page" title="Your preferences could not be loaded" />;
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
  if (patients.error) return <ErrorState error={patients.error} retry={() => void patients.refetch()} variant="page" title="Your patients could not be loaded" />;
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

const ACTION_LABEL: Record<string, string> = { add: "Add", remove: "Remove", replace: "Replace all" };

function SpecialtyRequestForm({ current, onDone }: { current: Json[]; onDone: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [action, setAction] = useState<"add" | "remove" | "replace">("add");
  const [chosen, setChosen] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  const send = useMutation({
    mutationFn: () => post("/me/specialty-requests", { action, specialties: chosen, notes: notes.trim() || null }),
    onSuccess: () => {
      toast("Request sent. An administrator will review it.");
      void client.invalidateQueries({ queryKey: ["me"] });
      void client.invalidateQueries({ queryKey: ["specialty-requests"] });
      onDone();
    },
  });
  return (
    <form
      className="space-y-4"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (chosen.length || action === "replace") send.mutate();
      }}
    >
      <p className="text-sm text-ink-muted">
        Currently: <span className="font-semibold text-ink">{specialtyText(current)}</span>. Nothing changes until an
        administrator approves.
      </p>
      <Segmented
        label="Change"
        value={action}
        onChange={setAction}
        options={[
          { value: "add", label: "Add" },
          { value: "remove", label: "Remove" },
          { value: "replace", label: "Replace all" },
        ]}
      />
      <SpecialtyPicker
        value={chosen}
        onChange={setChosen}
        legend={action === "add" ? "Specialties to add" : action === "remove" ? "Specialties to remove" : "Your specialties should be"}
      />
      <TextArea
        label="Notes (optional)"
        rows={3}
        maxLength={500}
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        hint="For example a board certification or where you trained."
      />
      <ErrorNote error={send.error} />
      <Button type="submit" variant="primary" busy={send.isPending} disabled={!chosen.length && action !== "replace"}>
        Send request
      </Button>
    </form>
  );
}

export function MyProfile() {
  const { user, can } = useAuth();
  const profile = useQuery({ queryKey: ["me"], queryFn: () => api("/me/profile"), refetchInterval: 30_000 });
  const requests = useQuery({
    queryKey: ["specialty-requests"],
    queryFn: () => api("/me/specialty-requests"),
    enabled: can(P.SELF_SPECIALTY_REQUEST),
    refetchInterval: 30_000,
  });
  const [asking, setAsking] = useState(false);
  const close = useCallback(() => setAsking(false), []);
  if (profile.isLoading) return <Loading label="Loading your profile" rows={3} />;
  if (profile.error) return <ErrorState error={profile.error} retry={() => void profile.refetch()} variant="page" title="Your profile could not be loaded" />;
  const p: Json = profile.data;
  const mine: Json[] = requests.data?.requests ?? [];
  const pending = mine.some((r) => r.status === "pending");
  return (
    <>
      <PageHeader title="Profile" subtitle="Your professional profile. Specialties are set by an administrator." />
      <div className="grid max-w-5xl items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Card>
          <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
            <Avatar name={p.name} size="lg" />
            <div className="min-w-0">
              <PersonName
                name={p.name}
                verified={user?.professionally_verified}
                source={user?.verification_source}
                className="max-w-full text-xl font-semibold text-ink"
              />
              <div className="mt-0.5 text-sm text-ink-muted">
                {specialtyText(p.specialties)}
                {user?.professionally_verified && " · Verified"}
              </div>
            </div>
          </div>
          <dl className="mt-6 grid gap-4 border-t border-line pt-5 sm:grid-cols-2">
            <div className="flex gap-3 sm:col-span-2">
              <Stethoscope className="mt-0.5 h-4 w-4 shrink-0 text-ink-subtle" aria-hidden />
              <div className="min-w-0">
                <dt className="mb-1 text-[13px] text-ink-subtle">Specialties</dt>
                <dd>
                  <SpecialtyChips list={p.specialties} />
                </dd>
              </div>
            </div>
            <div className="flex gap-3">
              <Building2 className="mt-0.5 h-4 w-4 shrink-0 text-ink-subtle" aria-hidden />
              <div className="min-w-0">
                <dt className="text-[13px] text-ink-subtle">Organization</dt>
                <dd className="break-words text-sm font-semibold text-ink">{p.organization ?? "Not recorded"}</dd>
              </div>
            </div>
            <div className="flex gap-3">
              <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-ink-subtle" aria-hidden />
              <div>
                <dt className="text-[13px] text-ink-subtle">Location</dt>
                <dd className="text-sm font-semibold text-ink">{p.city ? `${p.city}, ${p.state}` : "Not recorded"}</dd>
              </div>
            </div>
            {p.npi && (
              <div className="flex gap-3">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-ink-subtle" aria-hidden />
                <div>
                  <dt className="text-[13px] text-ink-subtle">NPI (synthetic)</dt>
                  <dd className="tabular text-sm font-semibold text-ink">{p.npi}</dd>
                </div>
              </div>
            )}
          </dl>
        </Card>
        {can(P.SELF_SPECIALTY_REQUEST) && (
          <Card
            title="Specialty changes"
            action={
              <Button size="sm" disabled={pending} onClick={() => setAsking(true)}>
                Request specialty change
              </Button>
            }
          >
            {pending && <p className="mb-3 text-[13px] text-ink-subtle">You can send a new request once this one is decided.</p>}
            {mine.length ? (
              <ul className="divide-y divide-line">
                {mine.map((r) => (
                  <li key={r.id} className="py-3 first:pt-0 last:pb-0">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm font-semibold text-ink">{ACTION_LABEL[r.action]}</span>
                      <Badge tone={r.status === "approved" ? "ok" : r.status === "rejected" ? "bad" : "warn"}>
                        {titleCase(r.status)}
                      </Badge>
                    </div>
                    <div className="mt-0.5 text-[13px] text-ink-subtle">
                      {specialtyText(r.previous)} → {specialtyText(r.requested)} · {fmtDate(r.created_at)}
                    </div>
                    {r.decision_notes && <p className="mt-1 text-[13px] text-ink-muted">{r.decision_notes}</p>}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-ink-subtle">No requests yet.</p>
            )}
          </Card>
        )}
      </div>
      {asking && (
        <Drawer title="Request specialty change" onClose={close}>
          <SpecialtyRequestForm current={p.specialties ?? []} onDone={close} />
        </Drawer>
      )}
    </>
  );
}
