import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, History, Mail, Send, ShieldCheck, ShieldOff } from "lucide-react";
import { useId, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, post } from "../api";
import type { Json } from "../api";
import { useAuth } from "../auth";
import { legalPath } from "../legal";
import { P } from "../permissions";
import { useToast } from "../toast";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorNote,
  ErrorState,
  FormField,
  LoadingRows,
  PageHeader,
  fmtDate,
  fmtDateTime,
} from "../ui";
import type { Column, Tone } from "../ui";

export const REQUEST_STATUS: Record<string, { tone: Tone; label: string }> = {
  submitted: { tone: "info", label: "Submitted" },
  in_review: { tone: "warn", label: "In review" },
  completed: { tone: "ok", label: "Completed" },
  rejected: { tone: "bad", label: "Rejected" },
};

export function RequestStatus({ status }: { status: string }) {
  const s = REQUEST_STATUS[status] ?? { tone: "neutral" as Tone, label: status };
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

const selectClass =
  "block h-11 w-full rounded-lg border border-line-strong bg-surface px-3 text-[15px] text-ink shadow-card focus:border-primary focus:outline-none focus:ring-[3px] focus:ring-primary/20";

function Section({ title, description, icon, children }: { title: string; description?: ReactNode; icon: ReactNode; children: ReactNode }) {
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <span className="text-ink-subtle">{icon}</span>
          {title}
        </span>
      }
      description={description}
    >
      {children}
    </Card>
  );
}

function ConsentRow({ item, onWithdraw, busy }: { item: Json; onWithdraw: () => void; busy: boolean }) {
  const [confirming, setConfirming] = useState(false);
  const given = item.state === "accepted";
  return (
    <li className="py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-2xl">
          <div className="font-semibold text-ink">{item.label}</div>
          <div className="mt-0.5 text-[13px] text-ink-subtle">
            {given
              ? `Given for version ${item.version} on ${fmtDateTime(item.at)}`
              : item.state === "withdrawn"
                ? `Withdrawn on ${fmtDateTime(item.at)}`
                : "Not given"}
            {item.document && (
              <>
                {" · "}
                <Link to={legalPath(item.document)} className="font-medium text-primary-ink underline underline-offset-2">
                  Read it
                </Link>
              </>
            )}
          </div>
          {item.statement && <p className="mt-2 text-sm leading-6 text-ink-muted">{item.statement}</p>}
        </div>
        <Badge tone={item.satisfied ? "ok" : "warn"} icon={item.satisfied ? <ShieldCheck className="h-3.5 w-3.5" aria-hidden /> : undefined}>
          {item.satisfied ? "In effect" : "Not in effect"}
        </Badge>
      </div>
      {item.withdrawable && given && !confirming && (
        <Button size="sm" variant="quiet-danger" className="mt-3" onClick={() => setConfirming(true)}>
          <ShieldOff className="h-3.5 w-3.5" aria-hidden /> Withdraw consent
        </Button>
      )}
      {confirming && (
        <Alert tone="warn" title="Withdraw this consent?" className="mt-3">
          <p>
            Your health information will no longer be used to provide adherence support, and your account will be limited
            to Data & privacy until you give consent again. A restriction request is opened so a person reviews what
            happens to the information already held. Withdrawal does not affect processing that happened before it.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="danger" busy={busy} onClick={onWithdraw}>
              Withdraw
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              Keep consent
            </Button>
          </div>
        </Alert>
      )}
      {!item.withdrawable && given && (
        <p className="mt-2 text-[13px] text-ink-subtle">
          Required to use the service. To stop, request deletion of your account below.
        </p>
      )}
    </li>
  );
}

function RequestForm({ types }: { types: Record<string, string> }) {
  const client = useQueryClient();
  const toast = useToast();
  const typeId = useId();
  const detailsId = useId();
  const [type, setType] = useState("access");
  const [details, setDetails] = useState("");
  const submit = useMutation({
    mutationFn: () => post("/privacy/requests", { type, details: details.trim() || null }),
    onSuccess: () => {
      toast("Request submitted. A person will review it.");
      setDetails("");
      void client.invalidateQueries({ queryKey: ["privacy"] });
    },
  });
  return (
    <form
      className="space-y-4"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        submit.mutate();
      }}
    >
      <FormField label="What would you like?" htmlFor={typeId}>
        <select id={typeId} className={selectClass} value={type} onChange={(e) => setType(e.target.value)}>
          {Object.entries(types).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </FormField>
      <FormField label="Details (optional)" htmlFor={detailsId} hint="Don't include health information here.">
        <textarea
          id={detailsId}
          value={details}
          maxLength={2000}
          rows={3}
          onChange={(e) => setDetails(e.target.value)}
          className="block w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[15px] text-ink shadow-card focus:border-primary focus:outline-none focus:ring-[3px] focus:ring-primary/20"
        />
      </FormField>
      <ErrorNote error={submit.error} />
      <Button type="submit" variant="primary" busy={submit.isPending}>
        <Send className="h-4 w-4" aria-hidden /> Submit request
      </Button>
      <p className="text-[13px] leading-5 text-ink-subtle">
        Submitting records your request; it does not change or delete anything by itself. You can follow its status
        below.
      </p>
    </form>
  );
}

async function downloadExport() {
  const data = await api("/privacy/export");
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = Object.assign(document.createElement("a"), { href: url, download: "my-data.json" });
  link.click();
  URL.revokeObjectURL(url);
}

export default function Privacy() {
  const client = useQueryClient();
  const { can } = useAuth();
  const toast = useToast();
  const status = useQuery({ queryKey: ["privacy", "status"], queryFn: () => api("/privacy/status") });
  const requests = useQuery({ queryKey: ["privacy", "requests"], queryFn: () => api<Json[]>("/privacy/requests") });
  const history = useQuery({ queryKey: ["privacy", "history"], queryFn: () => api<Json[]>("/privacy/history") });
  const withdraw = useMutation({
    mutationFn: (kind: string) => post("/privacy/withdraw", { kind }),
    onSuccess: () => {
      toast("Consent withdrawn.");
      void client.invalidateQueries();
    },
  });
  const exporting = useMutation({ mutationFn: downloadExport });

  if (status.isLoading) return <LoadingRows rows={6} label="Loading your privacy settings" />;
  if (status.error)
    return <ErrorState error={status.error} retry={() => void status.refetch()} variant="page" title="Privacy settings could not be loaded" />;
  const s: Json = status.data;

  const requestColumns: Column<Json>[] = [
    { key: "type", header: "Request", primary: true, cell: (r) => <span className="font-medium text-ink">{r.type_label}</span> },
    { key: "status", header: "Status", hideOnMobile: true, cell: (r) => <RequestStatus status={r.status} /> },
    { key: "created", header: "Submitted", cell: (r) => <span className="tabular text-ink-muted">{fmtDateTime(r.created_at)}</span> },
    {
      key: "resolution",
      header: "Outcome",
      cell: (r) => <span className="text-ink-muted">{r.resolution ?? (r.respond_by ? `Response by ${fmtDate(r.respond_by)}` : "Pending")}</span>,
    },
  ];
  const historyColumns: Column<Json>[] = [
    { key: "label", header: "Item", primary: true, cell: (h) => <span className="font-medium text-ink">{h.label}</span> },
    { key: "action", header: "Action", cell: (h) => <Badge tone={h.action === "accepted" ? "ok" : "warn"}>{h.action === "accepted" ? "Accepted" : "Withdrawn"}</Badge> },
    { key: "version", header: "Version", cell: (h) => <span className="tabular text-ink-muted">{h.version}</span> },
    { key: "source", header: "Where", hideOnMobile: true, cell: (h) => <span className="text-ink-muted">{h.source}</span> },
    { key: "at", header: "When", cell: (h) => <span className="tabular text-ink-muted">{fmtDateTime(h.at)}</span> },
  ];

  return (
    <>
      <PageHeader
        title="Data & privacy"
        subtitle="The documents you accepted, your consents, a copy of your data, and requests about your personal information."
      />
      {/* Consents first, across the full width (their number differs by role); the documents (with
          the privacy contact) and the data copy as one even row; then privacy requests and the consent history. */}
          <Section title="Your consents" icon={<ShieldCheck className="h-4 w-4" aria-hidden />}>
            <ErrorNote error={withdraw.error} className="mb-3" />
            <ul className="divide-y divide-line">
              {s.consents.map((c: Json) => (
                <ConsentRow key={c.kind} item={c} busy={withdraw.isPending} onWithdraw={() => withdraw.mutate(c.kind)} />
              ))}
            </ul>
            {can(P.SELF_CONSENT_MANAGE) && (
              <p className="mt-4 border-t border-line pt-4 text-sm text-ink-muted">
                How we may contact you, and whether your healthcare professional may see your adherence, are set in{" "}
                <Link to="/consent" className="font-semibold text-primary-ink underline underline-offset-2">
                  Consent & preferences
                </Link>
                .
              </p>
            )}
          </Section>



      <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <Section title="Documents" icon={<FileText className="h-4 w-4" aria-hidden />}>
            <ul className="space-y-3">
              {s.documents.map((d: Json) => (
                <li key={d.kind} className="flex flex-wrap items-baseline justify-between gap-2">
                  <Link to={legalPath(d.kind)} className="font-semibold text-primary-ink underline-offset-2 hover:underline">
                    {d.title}
                  </Link>
                  <span className="text-[13px] text-ink-subtle">
                    Version {d.version} · effective {fmtDate(d.effective_date)}
                    {d.draft && " · draft"}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-4 flex gap-2 border-t border-line pt-4 text-sm leading-6 text-ink-muted">
              <Mail className="mt-1 h-4 w-4 shrink-0 text-ink-subtle" aria-hidden />
              <span>
              Questions about your privacy: see{" "}
              <Link to={`${legalPath("privacy")}#who-we-are`} className="font-semibold text-primary-ink underline underline-offset-2">
                who we are and how to contact us
              </Link>
              . You can also complain to a data protection authority where that right applies.
              </span>
            </p>
          </Section>


          <Section
            title="A copy of your data"
            icon={<Download className="h-4 w-4" aria-hidden />}
            description="Your account details, consent history and requests, and the record you see in your portal, as a JSON file."
          >
            <Button busy={exporting.isPending} onClick={() => exporting.mutate()}>
              <Download className="h-4 w-4" aria-hidden /> Download my data
            </Button>
            <ErrorNote error={exporting.error} className="mt-3" />
            <p className="mt-3 text-[13px] leading-5 text-ink-subtle">
              Internal scores and recommendations prepared for staff are not in this file. Submit an access request to
              receive them after review.
            </p>
          </Section>

      </div>

      <div className="mt-4 space-y-4 [&:not(:has(*))]:hidden">
          {can(P.PRIVACY_REQUEST) ? (
          <Section
            title="Privacy requests"
            icon={<Send className="h-4 w-4" aria-hidden />}
            description="Ask to access, correct, delete, restrict, receive or object to the processing of your personal information."
          >
            {/* The form and the requests already made sit side by side when there is room. */}
            <div className="grid gap-6 xl:grid-cols-2">
            <RequestForm types={s.request_types} />
            <div className="border-t border-line pt-4 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0">
              {requests.isLoading ? (
                <LoadingRows rows={2} label="Loading requests" />
              ) : requests.data?.length ? (
                <DataTable caption="Your privacy requests" tableFrom="2xl" columns={requestColumns} rows={requests.data} rowKey={(r) => r.id} mobileAside={(r) => <RequestStatus status={r.status} />} />
              ) : (
                <EmptyState compact title="No requests yet" icon={<Send className="h-5 w-5" />}>
                  Requests you submit appear here with their status.
                </EmptyState>
              )}
            </div>
            </div>
          </Section>
          ) : (
            can(P.PRIVACY_MANAGE) && (
              <Section title="Privacy requests" icon={<Send className="h-4 w-4" aria-hidden />}>
                <p className="text-sm text-ink-muted">
                  You handle privacy requests; you do not submit them here. Review and work them in the{" "}
                  <Link to="/privacy-requests" className="font-semibold text-primary-ink underline underline-offset-2">
                    request centre
                  </Link>
                  .
                </p>
              </Section>
            )
          )}
      </div>

      <div className="mt-4">
        <Section title="Consent history" icon={<History className="h-4 w-4" aria-hidden />} description="Every acceptance and withdrawal, with the version. Entries are never changed.">
          {history.data?.length ? (
            <DataTable caption="Consent history" tableFrom="2xl" columns={historyColumns} rows={history.data} rowKey={(h) => `${h.kind}-${h.at}-${h.action}`} />
          ) : (
            <EmptyState compact title="No history yet" icon={<History className="h-5 w-5" />} />
          )}
        </Section>
      </div>
    </>
  );
}
