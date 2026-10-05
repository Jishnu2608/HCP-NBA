import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, ShieldCheck, UserCheck, UserCog, UserX, Users as UsersIcon, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api, patch, put, query } from "../api";
import type { Json } from "../api";
import { ROLE_LABEL, useAuth } from "../auth";
import type { Role } from "../auth";
import { P } from "../permissions";
import { useToast } from "../toast";
import { DeleteAccount } from "./DeleteAccount";
import { SpecialtyPicker, specialtyText } from "./HealthForms";
import { Link } from "react-router-dom";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorNote,
  ErrorState,
  LoadingRows,
  PageHeader,
  SearchInput,
  Select,
  KpiGrid,
  PersonName,
  Stat,
  Toolbar,
  Drawer,
  fmtDate,
  fmtDateTime,
  num,
  titleCase,
} from "../ui";
import type { Column, Tone } from "../ui";

const STATUS: Record<string, { tone: Tone; label: string; icon: ReactNode }> = {
  active: { tone: "ok", label: "Active", icon: <UserCheck className="h-3.5 w-3.5" aria-hidden /> },
  pending: { tone: "warn", label: "Pending verification", icon: <UserCog className="h-3.5 w-3.5" aria-hidden /> },
  disabled: { tone: "bad", label: "Disabled", icon: <UserX className="h-3.5 w-3.5" aria-hidden /> },
};
const SOURCE_LABEL: Record<string, string> = {
  system: "System",
  seed: "Demo account",
  signup: "Registered patient",
  invitation: "Invited",
};
const AGE_LABEL: Record<string, string> = { minor: "Minor", adult: "Adult", unknown: "Not recorded" };

/** Who brought an account in, from the administrator (or the platform) down to it. */
function Lineage({ chain, source }: { chain: Json[]; source: string }) {
  if (source === "seed" || source === "system") {
    return <p className="text-sm text-ink-muted">Provisioned by the platform, not through an invitation.</p>;
  }
  if (source === "signup") {
    return <p className="text-sm text-ink-muted">Registered as a patient. Patients do not need an invitation.</p>;
  }
  return (
    <ol className="space-y-2">
      {chain.map((p, i) => (
        <li key={p.id} className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-subtle text-[11px] font-semibold text-ink-muted ring-1 ring-inset ring-line"
          >
            {i + 1}
          </span>
          <PersonName
            name={p.name}
            verified={p.professionally_verified}
            source={p.verification_source}
            className="text-sm font-semibold text-ink"
          />
          <span className="truncate text-[13px] text-ink-subtle">{p.role_label}</span>
        </li>
      ))}
    </ol>
  );
}

function AccountStatus({ status }: { status: string }) {
  const s = STATUS[status] ?? { tone: "neutral" as Tone, label: titleCase(status), icon: null };
  return (
    <Badge tone={s.tone} icon={s.icon}>
      {s.label}
    </Badge>
  );
}

function assignmentText(a: Json) {
  if (!a.kind) return "None for this role";
  if (a.kind === "patients") return `${a.count} patients`;
  if (a.kind === "hcps") return `${a.count} HCPs`;
  return a.id ?? "Not linked";
}

/** "patient:read:assigned" becomes "Patient · read · assigned". */
const permissionLabel = (p: string) => p.split(":").map((part, i) => (i === 0 ? titleCase(part) : part.replace(/_/g, " "))).join(" · ");

/** Search box that adds a patient or an HCP to a selection. */
function RecordPicker({
  kind,
  exclude,
  onPick,
}: {
  kind: "patient" | "hcp";
  exclude: string[];
  onPick: (record: { id: string; name: string }) => void;
}) {
  const [q, setQ] = useState("");
  const search = useQuery({
    queryKey: ["picker", kind, q],
    enabled: q.trim().length >= 2,
    queryFn: () => api(`/${kind === "patient" ? "patients" : "hcps"}${query({ q, limit: 8 })}`),
  });
  const idKey = kind === "patient" ? "patient_id" : "hcp_id";
  const results: Json[] = (search.data?.items ?? []).filter((r: Json) => !exclude.includes(r[idKey]));
  return (
    <div>
      <SearchInput
        label={`Search ${kind === "patient" ? "patients" : "HCPs"} to add`}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder={`Add ${kind === "patient" ? "a patient" : "an HCP"} by name or ID`}
      />
      {q.trim().length >= 2 && (
        <ul className="mt-1.5 max-h-64 divide-y divide-line overflow-y-auto rounded-lg border border-line bg-surface shadow-raised">
          {search.isLoading && <li className="px-3 py-2.5 text-sm text-ink-subtle">Searching</li>}
          {!search.isLoading && !results.length && <li className="px-3 py-2.5 text-sm text-ink-subtle">No matches</li>}
          {results.map((r) => (
            <li key={r[idKey]} className="flex items-center justify-between gap-2 px-3 py-1.5 text-sm">
              <span className="min-w-0 truncate">
                {r.name} <span className="tabular text-xs text-ink-subtle">{r[idKey]}</span>
              </span>
              <Button size="sm" variant="ghost" onClick={() => onPick({ id: r[idKey], name: r.name })}>
                <Plus className="h-3.5 w-3.5" aria-hidden /> Add
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AssignmentEditor({ account }: { account: Json }) {
  const client = useQueryClient();
  const toast = useToast();
  const kind: string | null = account.assignment.kind;
  const isPanel = kind === "patients" || kind === "hcps";
  const recordKind = kind === "patients" || kind === "patient" ? "patient" : "hcp";
  const idKey = recordKind === "patient" ? "patient_id" : "hcp_id";

  const initial: Array<{ id: string; name: string }> = isPanel
    ? (account[kind!] ?? []).map((r: Json) => ({ id: r[idKey], name: r.name }))
    : account[recordKind]
      ? [{ id: account[recordKind][idKey], name: account[recordKind].name }]
      : [];
  const [selected, setSelected] = useState(initial);
  useEffect(() => setSelected(initial), [account.id, JSON.stringify(initial)]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: () => {
      const ids = selected.map((s) => s.id);
      const body = isPanel
        ? { [recordKind === "patient" ? "patient_ids" : "hcp_ids"]: ids }
        : { [idKey]: ids[0] ?? null };
      return put(`/admin/users/${account.id}/assignments`, body);
    },
    onSuccess: () => {
      toast("Assignments saved. The role and permissions are unchanged.");
      void client.invalidateQueries({ queryKey: ["users"] });
    },
  });

  if (!kind) {
    return <p className="text-sm text-ink-subtle">This role works without assigned records.</p>;
  }
  const changed = JSON.stringify(selected.map((s) => s.id)) !== JSON.stringify(initial.map((s) => s.id));

  return (
    <div className="space-y-3">
      <p className="text-[13px] text-ink-subtle">
        {isPanel
          ? `The ${recordKind === "patient" ? "patients" : "HCPs"} this account may see and act on.`
          : `The single ${recordKind === "patient" ? "patient" : "HCP"} record this account sees as its own.`}{" "}
        Changing this changes data scope only.
      </p>
      {selected.length ? (
        <ul className="flex max-h-48 flex-wrap gap-1.5 overflow-y-auto">
          {selected.map((s) => (
            <li
              key={s.id}
              className="flex max-w-full items-center gap-1.5 rounded-md bg-subtle py-1 pl-2.5 pr-1 text-xs text-ink ring-1 ring-inset ring-line"
            >
              <span className="truncate">{s.name}</span>
              <span className="tabular shrink-0 text-ink-subtle">{s.id}</span>
              <button
                type="button"
                aria-label={`Remove ${s.name}`}
                onClick={() => setSelected(selected.filter((x) => x.id !== s.id))}
                className="grid h-6 w-6 shrink-0 place-items-center rounded hover:bg-sunken"
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-warn">Nothing assigned. This account will see an empty workspace.</p>
      )}
      <RecordPicker
        kind={recordKind}
        exclude={selected.map((s) => s.id)}
        onPick={(record) => setSelected(isPanel ? [...selected, record] : [record])}
      />
      <ErrorNote error={save.error} />
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button variant="primary" disabled={!changed} busy={save.isPending} onClick={() => save.mutate()}>
          Save assignments
        </Button>
        {changed && (
          <Button variant="ghost" onClick={() => setSelected(initial)}>
            Discard changes
          </Button>
        )}
      </div>
    </div>
  );
}

/** An HCP's specialties: set here by an administrator only (0..n from the controlled list). */
function SpecialtyEditor({ account }: { account: Json }) {
  const client = useQueryClient();
  const toast = useToast();
  const current: string[] = (account.hcp.specialties ?? []).map((s: Json) => s.code);
  const [value, setValue] = useState<string[]>(current);
  useEffect(() => setValue(current), [account.id, current.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: () => put(`/admin/users/${account.id}/specialties`, { specialties: value }),
    onSuccess: () => {
      toast("Specialties saved.");
      void client.invalidateQueries({ queryKey: ["users"] });
    },
  });
  const changed = [...value].sort().join("|") !== [...current].sort().join("|");
  return (
    <section className="border-t border-line pt-5">
      <h3 className="mb-1 text-sm font-semibold text-ink">Specialties</h3>
      <p className="mb-3 text-[13px] text-ink-subtle">
        Currently: {specialtyText(account.hcp.specialties)}. Used to route patients to this HCP. Only an administrator sets
        them; the HCP can request a change.
      </p>
      {account.hcp.pending_request ? (
        <Alert tone="warn" title="A change request is waiting">
          Decide it in{" "}
          <Link to="/privacy-requests?tab=specialties" className="font-semibold underline underline-offset-2">
            Requests
          </Link>{" "}
          before editing here.
        </Alert>
      ) : (
        <div className="space-y-3">
          <SpecialtyPicker value={value} onChange={setValue} legend="Specialties" />
          <ErrorNote error={save.error} />
          <Button variant="primary" disabled={!changed} busy={save.isPending} onClick={() => save.mutate()}>
            Save specialties
          </Button>
        </div>
      )}
    </section>
  );
}

function AccountPanel({ id, onDeleted }: { id: number; onDeleted: () => void }) {
  const { user: me, can } = useAuth();
  const client = useQueryClient();
  const toast = useToast();
  const detail = useQuery({ queryKey: ["users", id], queryFn: () => api(`/admin/users/${id}`) });
  const [confirmDisable, setConfirmDisable] = useState(false);
  // What disabling would change (a care manager's patients, an HCP's waiting consultations),
  // shown before the administrator confirms.
  const impact = useQuery({
    queryKey: ["users", id, "impact"],
    queryFn: () => api(`/admin/users/${id}/impact`),
    enabled: confirmDisable,
  });
  const setStatus = useMutation({
    mutationFn: (status: string) => patch(`/admin/users/${id}/status`, { status }),
    onSuccess: (_, status) => {
      toast(status === "disabled" ? "Account disabled and signed out everywhere" : "Account re-enabled");
      setConfirmDisable(false);
      void client.invalidateQueries({ queryKey: ["users"] });
      void client.invalidateQueries({ queryKey: ["attention"] });
      void client.invalidateQueries({ queryKey: ["unassigned"] });
    },
  });
  if (detail.isLoading) return <LoadingRows rows={6} label="Loading account" />;
  if (detail.error) return <ErrorState error={detail.error} retry={() => void detail.refetch()} variant="page" title="This account could not be loaded" />;
  const a: Json = detail.data;
  const locked = a.id === me!.id || a.source === "system";

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Avatar name={a.name} size="lg" />
        <div className="min-w-0">
          <PersonName
            name={a.name}
            verified={a.professionally_verified}
            source={a.verification_source}
            className="max-w-full text-lg font-semibold text-ink"
          />
          <div className="break-all text-sm text-ink-muted">{a.email}</div>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-4 rounded-xl border border-line bg-subtle/50 p-4">
        <div className="min-w-0">
          <dt className="text-[13px] text-ink-subtle">Role</dt>
          <dd className="mt-0.5 flex items-center gap-1.5 text-sm font-semibold text-ink">
            <span className="truncate">{ROLE_LABEL[a.role as Role]}</span>
            <Lock className="h-3.5 w-3.5 shrink-0 text-ink-subtle" aria-label="Fixed" />
          </dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Status</dt>
          <dd className="mt-1">
            <AccountStatus status={a.status} />
          </dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Account type</dt>
          <dd className="mt-0.5 text-sm font-semibold text-ink">{SOURCE_LABEL[a.source]}</dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Email verified</dt>
          <dd className="mt-0.5 text-sm font-semibold text-ink">{a.email_verified ? "Yes" : "Not yet"}</dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Professional verification</dt>
          <dd className="mt-0.5 text-sm font-semibold text-ink">
            {a.professionally_verified
              ? a.verification_source === "system"
                ? "Verified · platform"
                : "Verified · invitation"
              : "Not applicable"}
          </dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Country of residence</dt>
          <dd className="mt-0.5 text-sm font-semibold text-ink">{a.location ?? "Not recorded"}</dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Age</dt>
          <dd className="mt-0.5 text-sm font-semibold text-ink">{AGE_LABEL[a.age_band] ?? "Not recorded"}</dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Created</dt>
          <dd className="tabular mt-0.5 text-sm font-semibold text-ink">{fmtDate(a.created_at)}</dd>
        </div>
        <div>
          <dt className="text-[13px] text-ink-subtle">Last sign-in</dt>
          <dd className="tabular mt-0.5 text-sm font-semibold text-ink">
            {a.last_login_at ? fmtDateTime(a.last_login_at) : "Never"}
          </dd>
        </div>
      </dl>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-ink">How this account joined</h3>
        <Lineage chain={a.lineage ?? []} source={a.source} />
      </section>

      <section className="border-t border-line pt-5">
        <h3 className="text-sm font-semibold text-ink">What this role can do</h3>
        <p className="mt-0.5 text-[13px] text-ink-subtle">
          Fixed when the account was created (by its invitation, or patient sign-up). Permissions come only from the
          role and cannot be edited here.
        </p>
        <ul className="mt-3 flex flex-wrap gap-1.5">
          {a.permissions.map((p: string) => (
            <li key={p}>
              <Badge tone="sage" icon={<ShieldCheck className="h-3 w-3" aria-hidden />}>
                {permissionLabel(p)}
              </Badge>
            </li>
          ))}
        </ul>
      </section>

      {a.hcp && <SpecialtyEditor account={a} />}

      <section className="border-t border-line pt-5">
        <h3 className="mb-2 text-sm font-semibold text-ink">Assignments</h3>
        <AssignmentEditor account={a} />
      </section>

      <section className="border-t border-line pt-5">
        <h3 className="mb-2 text-sm font-semibold text-ink">Account status</h3>
        <ErrorNote error={setStatus.error} className="mb-3" />
        {locked ? (
          <p className="text-sm text-ink-subtle">
            {a.id === me!.id ? "You cannot change your own account." : "The system administrator cannot be disabled."}
          </p>
        ) : a.status === "disabled" ? (
          <Button busy={setStatus.isPending} onClick={() => setStatus.mutate("active")}>
            <UserCheck className="h-4 w-4" aria-hidden /> Re-enable account
          </Button>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            {a.status === "pending" && <span className="text-sm text-ink-subtle">Waiting for email verification.</span>}
            {!confirmDisable && (
              <Button variant="quiet-danger" onClick={() => setConfirmDisable(true)}>
                <UserX className="h-4 w-4" aria-hidden /> Disable account
              </Button>
            )}
          </div>
        )}
        {confirmDisable && a.status !== "disabled" && (
          <Alert tone="warn" title="Disable this account?" className="mt-3">
            {impact.isLoading ? (
              <p>Checking what this changes…</p>
            ) : (
              <ul className="list-disc space-y-1 pl-5">
                <li>The account is signed out everywhere and cannot sign in.</li>
                {impact.data?.real_patients > 0 &&
                  (impact.data.to ? (
                    <li>
                      {impact.data.real_patients} registered patient{impact.data.real_patients === 1 ? "" : "s"} and{" "}
                      {impact.data.open_requests} open request{impact.data.open_requests === 1 ? "" : "s"} move to{" "}
                      <span className="font-semibold">{impact.data.to.name}</span>.
                    </li>
                  ) : (
                    <li className="font-semibold">
                      No other active care manager can take this account's {impact.data.real_patients} registered
                      patients, so it cannot be disabled yet.
                    </li>
                  ))}
                {impact.data?.consultations > 0 && (
                  <li>
                    {impact.data.consultations} consultation{impact.data.consultations === 1 ? "" : "s"} waiting for this
                    HCP go back to the care manager to route again.
                  </li>
                )}
              </ul>
            )}
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="danger"
                busy={setStatus.isPending}
                disabled={impact.isLoading || (impact.data?.real_patients > 0 && !impact.data?.to)}
                onClick={() => setStatus.mutate("disabled")}
              >
                Disable account
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmDisable(false)}>
                Cancel
              </Button>
            </div>
          </Alert>
        )}
        <p className="mt-2 text-[13px] text-ink-subtle">Disabling signs the account out everywhere at once and blocks sign-in.</p>
      </section>

      {/* Patients only (the server refuses any other account). */}
      {can(P.USER_DELETE) && !locked && a.permissions.includes(P.SELF_HEALTH_MANAGE) && (
        <section className="border-t border-line pt-5">
          <h3 className="mb-1 text-sm font-semibold text-ink">Delete account</h3>
          <p className="mb-3 text-[13px] text-ink-subtle">
            For a patient who asked to be deleted. Removes the account and the patient's own data; not reversible.
          </p>
          <DeleteAccount account={a} onDeleted={onDeleted} />
        </section>
      )}
    </div>
  );
}

export default function UsersPage() {
  const [role, setRole] = useState("");
  const [source, setSource] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const close = useCallback(() => setOpen(null), []);
  const list = useQuery({
    queryKey: ["users", "list", role, source, q],
    queryFn: () => api(`/admin/users${query({ role, source, q, limit: 200 })}`),
    placeholderData: (previous) => previous,
  });
  const items: Json[] = list.data?.items ?? [];
  const byRole: Record<string, number> = list.data?.by_role ?? {};
  const totalAccounts = Object.values(byRole).reduce((a, b) => a + b, 0);
  const countStatus = (s: string) => items.filter((u) => u.status === s).length;
  const shown = (n: number) => (list.data ? num(n) : "—");

  const columns: Column<Json>[] = [
    {
      key: "account",
      header: "Account",
      primary: true,
      className: "min-w-64",
      cell: (u) => (
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={u.name} size="sm" />
          <div className="min-w-0">
            <PersonName
              name={u.name}
              verified={u.professionally_verified}
              source={u.verification_source}
              className="max-w-full font-semibold text-ink"
            />
            <div className="truncate text-[13px] text-ink-subtle" title={u.email}>
              {u.email}
            </div>
          </div>
        </div>
      ),
    },
    {
      key: "role",
      header: "Role",
      cell: (u) => (
        <span className="text-ink">
          {ROLE_LABEL[u.role as Role]}
          {u.age_band === "minor" && (
            <Badge tone="info" className="ml-1.5">
              Minor
            </Badge>
          )}
        </span>
      ),
    },
    {
      key: "invited_by",
      header: "Invited by",
      hideOnMobile: true,
      cell: (u) =>
        u.invited_by ? (
          <PersonName
            name={u.invited_by.name}
            verified={u.invited_by.professionally_verified}
            source={u.invited_by.verification_source}
            className="text-ink-muted"
          />
        ) : (
          <span className="text-ink-subtle">{u.source === "seed" || u.source === "system" ? "Platform" : "—"}</span>
        ),
    },
    {
      key: "location",
      header: "Country",
      hideOnMobile: true,
      cell: (u) => <span className="text-ink-muted">{u.location ?? "—"}</span>,
    },
    { key: "status", header: "Status", hideOnMobile: true, cell: (u) => <AccountStatus status={u.status} /> },
    { key: "assigned", header: "Assigned", cell: (u) => <span className="text-ink-muted">{assignmentText(u.assignment)}</span> },
    { key: "type", header: "Type", cell: (u) => <span className="text-ink-subtle">{SOURCE_LABEL[u.source]}</span> },
  ];

  return (
    <>
      <PageHeader
        title="Users and assignments"
        subtitle="Every account, its role and what it is assigned to. Assignments and status can be changed here. Roles cannot: permissions come only from the role an account was created with."
      />
      <UnassignedPatients />
      <KpiGrid>
        <Stat label="Accounts" value={shown(totalAccounts)} icon={<UsersIcon className="h-4 w-4" aria-hidden />} hint={role || source || q ? "across all filters" : undefined} />
        <Stat label="Active" value={shown(countStatus("active"))} tone="ok" icon={<UserCheck className="h-4 w-4" aria-hidden />} hint="in this view" />
        <Stat label="Pending verification" value={shown(countStatus("pending"))} tone={countStatus("pending") ? "warn" : undefined} icon={<UserCog className="h-4 w-4" aria-hidden />} hint="in this view" />
        <Stat label="Disabled" value={shown(countStatus("disabled"))} tone={countStatus("disabled") ? "bad" : undefined} icon={<UserX className="h-4 w-4" aria-hidden />} hint="in this view" />
      </KpiGrid>
      <Toolbar>
        <div className="grid gap-2 sm:grid-cols-2 lg:flex">
          <Select label="Role" value={role} onChange={(e) => setRole(e.target.value)} className="lg:w-64">
            <option value="">All roles</option>
            {Object.entries(ROLE_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label} ({byRole[value] ?? 0})
              </option>
            ))}
          </Select>
          <Select label="Account type" value={source} onChange={(e) => setSource(e.target.value)} className="lg:w-52">
            <option value="">All account types</option>
            <option value="invitation">Invited</option>
            <option value="signup">Registered patients</option>
            <option value="seed">Demo accounts</option>
            <option value="system">System</option>
          </Select>
        </div>
        <SearchInput
          label="Search accounts"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name or email"
          className="w-full lg:w-72"
        />
      </Toolbar>

      <Card flush>
        {list.isLoading ? (
          <div className="p-5">
            <LoadingRows rows={8} label="Loading accounts" />
          </div>
        ) : list.error ? (
          <ErrorState error={list.error} retry={() => void list.refetch()} title="Accounts could not be loaded" />
        ) : !items.length ? (
          <EmptyState title="No accounts match" icon={<UsersIcon className="h-5 w-5" />}>
            Clear the filters or search by another name or email.
          </EmptyState>
        ) : (
          <DataTable
            caption="Accounts"
            tableFrom="4xl"
            columns={columns}
            rows={items}
            rowKey={(u) => u.id}
            onRowClick={(u) => setOpen(u.id)}
            rowClassName={(u) => open === u.id && "bg-primary-soft/50"}
            mobileAside={(u) => <AccountStatus status={u.status} />}
          />
        )}
      </Card>

      {open !== null && (
        <Drawer title="Account details" onClose={close}>
          <AccountPanel id={open} onDeleted={close} />
        </Drawer>
      )}
    </>
  );
}

/** Registered patients nobody is looking after (no active care manager when they joined).
 *  They are assigned automatically as soon as a care manager is active again. */
function UnassignedPatients() {
  const waiting = useQuery({ queryKey: ["unassigned"], queryFn: () => api<Json[]>("/admin/users/attention/unassigned") });
  if (!waiting.data?.length) return null;
  return (
    <Alert tone="bad" title={`${waiting.data.length} registered patient${waiting.data.length === 1 ? "" : "s"} without a care manager`} className="mb-6">
      Their requests reach nobody until a care manager is active. Re-enable or invite a care manager: they are assigned
      automatically. Waiting: {waiting.data.map((p) => `${p.name} (${p.patient_id})`).join(", ")}.
    </Alert>
  );
}
