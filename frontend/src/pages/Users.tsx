import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api, patch, put, query } from "../api";
import type { Json } from "../api";
import { ROLE_LABEL, useAuth } from "../auth";
import type { Role } from "../auth";
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
  cx,
  fmtDate,
  fmtDateTime,
} from "../ui";

const STATUS_TONE: Record<string, "good" | "warn" | "bad"> = {
  active: "good",
  pending: "warn",
  disabled: "bad",
};
const SOURCE_LABEL: Record<string, string> = {
  system: "System",
  seed: "Demo account",
  signup: "Registered",
};

function assignmentText(a: Json) {
  if (!a.kind) return "None for this role";
  if (a.kind === "patients") return `${a.count} patients`;
  if (a.kind === "hcps") return `${a.count} HCPs`;
  return a.id ?? "Not linked";
}

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
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder={`Search ${kind === "patient" ? "patients" : "HCPs"} by name or ID`}
        className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
      />
      {q.trim().length >= 2 && (
        <ul className="mt-1 divide-y divide-stone-100 rounded-lg border border-stone-200">
          {search.isLoading && <li className="px-3 py-2 text-sm text-stone-500">Searching</li>}
          {!search.isLoading && !results.length && (
            <li className="px-3 py-2 text-sm text-stone-500">No matches</li>
          )}
          {results.map((r) => (
            <li key={r[idKey]} className="flex items-center justify-between px-3 py-1.5 text-sm">
              <span>
                {r.name} <span className="text-xs text-stone-400">{r[idKey]}</span>
              </span>
              <button
                onClick={() => onPick({ id: r[idKey], name: r.name })}
                className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-brand-700 hover:bg-brand-50"
              >
                <Plus className="h-3.5 w-3.5" /> Add
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AssignmentEditor({ account }: { account: Json }) {
  const client = useQueryClient();
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
    onSuccess: () => void client.invalidateQueries({ queryKey: ["users"] }),
  });

  if (!kind) {
    return <p className="text-sm text-stone-500">This role works without assigned records.</p>;
  }
  const changed = JSON.stringify(selected.map((s) => s.id)) !== JSON.stringify(initial.map((s) => s.id));

  return (
    <div className="space-y-3">
      <p className="text-xs text-stone-500">
        {isPanel
          ? `The ${recordKind === "patient" ? "patients" : "HCPs"} this account may see and act on.`
          : `The single ${recordKind === "patient" ? "patient" : "HCP"} record this account sees as its own.`}{" "}
        Changing this changes data scope only.
      </p>
      {selected.length ? (
        <ul className="flex flex-wrap gap-1.5">
          {selected.map((s) => (
            <li
              key={s.id}
              className="flex items-center gap-1.5 rounded-full bg-stone-100 py-1 pl-3 pr-1.5 text-xs text-stone-800"
            >
              {s.name} <span className="text-stone-400">{s.id}</span>
              <button
                aria-label={`Remove ${s.name}`}
                onClick={() => setSelected(selected.filter((x) => x.id !== s.id))}
                className="rounded-full p-0.5 hover:bg-stone-200"
              >
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-amber-700">Nothing assigned. This account will see an empty workspace.</p>
      )}
      <RecordPicker
        kind={recordKind}
        exclude={selected.map((s) => s.id)}
        onPick={(record) => setSelected(isPanel ? [...selected, record] : [record])}
      />
      <ErrorNote error={save.error} />
      <div className="flex items-center gap-2">
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

function AccountPanel({ id, onClose }: { id: number; onClose: () => void }) {
  const { user: me } = useAuth();
  const client = useQueryClient();
  const detail = useQuery({ queryKey: ["users", id], queryFn: () => api(`/admin/users/${id}`) });
  const setStatus = useMutation({
    mutationFn: (status: string) => patch(`/admin/users/${id}/status`, { status }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["users"] }),
  });
  if (detail.isLoading) return <Loading />;
  if (detail.error) return <ErrorNote error={detail.error} />;
  const a: Json = detail.data;
  const locked = a.id === me!.id || a.source === "system";

  return (
    <Card
      title={a.name}
      action={
        <button onClick={onClose} aria-label="Close" className="rounded-md p-1 text-stone-500 hover:bg-stone-100">
          <X className="h-4 w-4" />
        </button>
      }
    >
      <dl className="grid grid-cols-2 gap-4">
        <Field label="Email">
          <span className="break-all">{a.email}</span>
        </Field>
        <Field label="Status">
          <Badge tone={STATUS_TONE[a.status]}>{a.status}</Badge>
          {!a.verified && <span className="ml-1.5 text-xs text-stone-500">email not verified</span>}
        </Field>
        <Field label="Role">
          <span className="inline-flex items-center gap-1">
            {ROLE_LABEL[a.role as Role]} <Lock className="h-3 w-3 text-stone-400" />
          </span>
        </Field>
        <Field label="Account type">{SOURCE_LABEL[a.source]}</Field>
        <Field label="Created">{fmtDate(a.created_at)}</Field>
        <Field label="Last sign-in">{a.last_login_at ? fmtDateTime(`${a.last_login_at}Z`) : "Never"}</Field>
      </dl>

      <div className="mt-4">
        <div className="text-xs font-medium uppercase tracking-wide text-stone-500">Permissions (from role)</div>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {a.permissions.map((p: string) => (
            <span key={p} className="rounded bg-stone-100 px-1.5 py-0.5 font-mono text-[11px] text-stone-700">
              {p}
            </span>
          ))}
        </div>
        <p className="mt-1.5 text-xs text-stone-500">
          The role is fixed at sign-up and permissions follow from it. Neither can be edited here.
        </p>
      </div>

      <div className="mt-5 border-t border-stone-100 pt-4">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-stone-500">Assignments</div>
        <AssignmentEditor account={a} />
      </div>

      <div className="mt-5 border-t border-stone-100 pt-4">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-stone-500">Account status</div>
        <ErrorNote error={setStatus.error} />
        {locked ? (
          <p className="text-sm text-stone-500">
            {a.id === me!.id ? "You cannot change your own account." : "The system administrator cannot be disabled."}
          </p>
        ) : a.status === "disabled" ? (
          <Button busy={setStatus.isPending} onClick={() => setStatus.mutate("active")}>
            Re-enable account
          </Button>
        ) : a.status === "active" ? (
          <Button variant="danger" busy={setStatus.isPending} onClick={() => setStatus.mutate("disabled")}>
            Disable account
          </Button>
        ) : (
          <div className="flex items-center gap-3">
            <span className="text-sm text-stone-500">Waiting for email verification.</span>
            <Button variant="danger" busy={setStatus.isPending} onClick={() => setStatus.mutate("disabled")}>
              Disable
            </Button>
          </div>
        )}
        <p className="mt-2 text-xs text-stone-500">
          Disabling signs the account out everywhere at once and blocks sign-in.
        </p>
      </div>
    </Card>
  );
}

export default function UsersPage() {
  const [role, setRole] = useState("");
  const [source, setSource] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const list = useQuery({
    queryKey: ["users", "list", role, source, q],
    queryFn: () => api(`/admin/users${query({ role, source, q, limit: 200 })}`),
  });
  const select = "rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-sm";

  return (
    <>
      <PageHeader
        title="Users and assignments"
        subtitle="Every account, its role, and what it is assigned to. Assignments and status can be changed here. Roles cannot: an account's permissions come only from the role it was created with."
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select value={role} onChange={(e) => setRole(e.target.value)} className={select}>
          <option value="">All roles</option>
          {Object.entries(ROLE_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label} ({list.data?.by_role?.[value] ?? 0})
            </option>
          ))}
        </select>
        <select value={source} onChange={(e) => setSource(e.target.value)} className={select}>
          <option value="">All account types</option>
          <option value="signup">Registered</option>
          <option value="seed">Demo accounts</option>
          <option value="system">System</option>
        </select>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name or email"
          className="ml-auto w-64 rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-sm"
        />
      </div>

      <div className={cx("grid gap-5", open !== null && "xl:grid-cols-5")}>
        <Card className={cx(open !== null && "xl:col-span-3")}>
          {list.isLoading ? (
            <Loading />
          ) : list.error ? (
            <ErrorNote error={list.error} />
          ) : !list.data.items.length ? (
            <Empty>No accounts match.</Empty>
          ) : (
            <Table head={["Account", "Role", "Status", "Assigned", "Type"]}>
              {list.data.items.map((u: Json) => (
                <tr
                  key={u.id}
                  onClick={() => setOpen(u.id)}
                  className={cx("cursor-pointer hover:bg-stone-50", open === u.id && "bg-brand-50/60")}
                >
                  <td className="px-3 py-2.5">
                    <div className="font-medium text-stone-900">{u.name}</div>
                    <div className="text-xs text-stone-500">{u.email}</div>
                  </td>
                  <td className="px-3 py-2.5 text-stone-700">{ROLE_LABEL[u.role as Role]}</td>
                  <td className="px-3 py-2.5">
                    <Badge tone={STATUS_TONE[u.status]}>{u.status}</Badge>
                  </td>
                  <td className="px-3 py-2.5 text-stone-600">{assignmentText(u.assignment)}</td>
                  <td className="px-3 py-2.5 text-stone-500">{SOURCE_LABEL[u.source]}</td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
        {open !== null && (
          <div className="xl:col-span-2">
            <AccountPanel id={open} onClose={() => setOpen(null)} />
          </div>
        )}
      </div>
    </>
  );
}
