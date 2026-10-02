import { useQuery } from "@tanstack/react-query";
import { Check, FileCheck2, HeartPulse, Stethoscope, UserRound, Users } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { Button, ErrorNote, Loading, cx } from "../ui";
import AuthLayout, { TextField } from "./AuthLayout";

interface RoleOption {
  role: string;
  label: string;
  description: string;
}

const ROLE_ICON: Record<string, typeof Users> = {
  care_manager: HeartPulse,
  medical_rep: Users,
  compliance: FileCheck2,
  patient: UserRound,
  hcp: Stethoscope,
};

export default function Signup() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  // The selectable roles come from the server. The administrator is not among them.
  const roles = useQuery({ queryKey: ["signup-roles"], queryFn: () => api<RoleOption[]>("/auth/roles") });
  const [form, setForm] = useState({ name: "", email: "", password: "", confirm_password: "", role: "" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm({ ...form, [key]: e.target.value });

  const mismatch = form.confirm_password !== "" && form.password !== form.confirm_password;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!form.role) {
      setError(new Error("Choose a role to continue."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await signup(form);
      navigate("/signup/verify");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      wide
      title="Create your account"
      subtitle="Choose the role you work in. It decides what you can see and do, and it cannot be changed after sign-up."
    >
      <form onSubmit={submit} className="space-y-6">
        <fieldset>
          <legend className="mb-2 text-xs font-medium text-stone-600">Role</legend>
          {roles.isLoading ? (
            <Loading />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {(roles.data ?? []).map((option) => {
                const Icon = ROLE_ICON[option.role] ?? UserRound;
                const selected = form.role === option.role;
                return (
                  <button
                    type="button"
                    key={option.role}
                    role="radio"
                    aria-checked={selected}
                    onClick={() => setForm({ ...form, role: option.role })}
                    className={cx(
                      "flex items-start gap-3 rounded-xl border p-4 text-left transition-colors",
                      selected
                        ? "border-brand-500 bg-brand-50 ring-2 ring-brand-100"
                        : "border-stone-200 hover:border-stone-300",
                    )}
                  >
                    <span
                      className={cx(
                        "grid h-9 w-9 shrink-0 place-items-center rounded-lg",
                        selected ? "bg-brand-600 text-white" : "bg-stone-100 text-stone-600",
                      )}
                    >
                      {selected ? <Check className="h-5 w-5" /> : <Icon className="h-5 w-5" />}
                    </span>
                    <span>
                      <span className="block text-sm font-semibold text-stone-900">{option.label}</span>
                      <span className="mt-0.5 block text-xs text-stone-600">{option.description}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Full name" autoComplete="name" value={form.name} onChange={set("name")} required />
          <TextField
            label="Email"
            type="email"
            autoComplete="email"
            value={form.email}
            onChange={set("email")}
            required
            hint="A verification code is sent to this address."
          />
          <TextField
            label="Password"
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={set("password")}
            required
            hint="At least 8 characters, with a letter and a number."
          />
          <TextField
            label="Confirm password"
            type="password"
            autoComplete="new-password"
            value={form.confirm_password}
            onChange={set("confirm_password")}
            required
            hint={mismatch ? "The two passwords do not match." : undefined}
          />
        </div>

        <ErrorNote error={error} />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-stone-600">
            Already registered?{" "}
            <Link to="/login" className="font-medium text-brand-700 hover:underline">
              Log in
            </Link>
          </p>
          <Button type="submit" variant="primary" busy={busy} disabled={mismatch}>
            Create account
          </Button>
        </div>
      </form>
    </AuthLayout>
  );
}
