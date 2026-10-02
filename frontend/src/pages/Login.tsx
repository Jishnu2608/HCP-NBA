import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowRight } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { ROLE_LABEL, useAuth } from "../auth";
import type { Role, User } from "../auth";
import { Button, ErrorNote } from "../ui";

const ROLE_BLURB: Record<Role, string> = {
  care_manager: "Works the adherence queue for assigned patients.",
  patient: "Sees own medications, messages and consent.",
  medical_rep: "Works the recommendation queue for assigned HCPs.",
  hcp: "Receives content and sees consented patients' adherence.",
  compliance: "Reviews content, gate outcomes and the audit trail.",
  admin: "Sees everything; runs the engine and the demo clock.",
};
const ORDER: Role[] = ["care_manager", "patient", "medical_rep", "hcp", "compliance", "admin"];

export default function Login() {
  const { loginAs, login } = useAuth();
  const navigate = useNavigate();
  const personas = useQuery({ queryKey: ["personas"], queryFn: () => api<User[]>("/auth/personas") });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function enter(action: () => Promise<unknown>, key: string) {
    setBusy(key);
    setError(null);
    try {
      await action();
      navigate("/", { replace: true });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void enter(() => login(username, password), "form");
  };

  return (
    <div className="min-h-full bg-gradient-to-b from-brand-900 to-brand-700 px-6 py-10">
      <div className="mx-auto max-w-5xl">
        <div className="flex items-center gap-3 text-white">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-white/15">
            <Activity className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Next Best Action</h1>
            <p className="text-sm text-white/75">
              The right message, to the right person, on the right channel, at the right moment.
            </p>
          </div>
        </div>

        <div className="mt-8 rounded-2xl bg-white p-6 shadow-xl">
          <h2 className="text-base font-semibold text-stone-900">Choose a demo persona</h2>
          <p className="mt-1 text-sm text-stone-500">
            Each role sees only its own data and actions. Open a second browser tab to hold two
            personas side by side.
          </p>
          <div className="mt-5 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {ORDER.map((role) => {
              const people = (personas.data ?? []).filter((p) => p.role === role);
              if (!people.length) return null;
              return (
                <div key={role} className="rounded-xl border border-stone-200 p-4">
                  <div className="text-sm font-semibold text-stone-900">{ROLE_LABEL[role]}</div>
                  <div className="mt-0.5 text-xs text-stone-500">{ROLE_BLURB[role]}</div>
                  <div className="mt-3 space-y-1.5">
                    {people.map((p) => (
                      <button
                        key={p.username}
                        disabled={busy !== null}
                        onClick={() => void enter(() => loginAs(p.username), p.username)}
                        className="group flex w-full items-center justify-between rounded-lg border border-stone-200 px-3 py-2 text-left text-sm hover:border-brand-500 hover:bg-brand-50 disabled:opacity-60"
                      >
                        <span>
                          <span className="font-medium text-stone-800">{p.display_name}</span>
                          <span className="ml-2 text-xs text-stone-400">
                            {p.hcp_id ?? p.patient_id ?? p.username}
                          </span>
                        </span>
                        <ArrowRight className="h-4 w-4 text-stone-300 group-hover:text-brand-600" />
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>

          <form onSubmit={submit} className="mt-6 flex flex-wrap items-end gap-3 border-t border-stone-100 pt-5">
            <label className="text-sm">
              <span className="mb-1 block text-xs font-medium text-stone-500">Username</span>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-48 rounded-lg border border-stone-300 px-3 py-2 text-sm"
                autoComplete="username"
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-xs font-medium text-stone-500">Password</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-48 rounded-lg border border-stone-300 px-3 py-2 text-sm"
                autoComplete="current-password"
              />
            </label>
            <Button type="submit" variant="primary" busy={busy === "form"} disabled={!username || !password}>
              Sign in
            </Button>
          </form>
          <div className="mt-3">
            <ErrorNote error={error} />
          </div>
        </div>
        <p className="mt-4 text-center text-xs text-white/60">
          All people and records shown are synthetic. A communication-decision tool, not a clinical one.
        </p>
      </div>
    </div>
  );
}
