import { useQuery } from "@tanstack/react-query";
import { Check, FileCheck2, HeartPulse, Stethoscope, UserRound, Users } from "lucide-react";
import { useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { Button, ErrorNote, PasswordField, Skeleton, TextField, cx } from "../ui";
import AuthLayout from "./AuthLayout";

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

/** What each role's workspace contains, shown under the server's description. */
const ROLE_SCOPE: Record<string, string> = {
  care_manager: "Adherence queue and Patient 360 for your assigned patients",
  medical_rep: "HCP queue, HCP 360 and approved content for your assigned HCPs",
  compliance: "Content approval, blocked recommendations and the audit log",
  patient: "Your medications, messages and contact preferences",
  hcp: "Your inbox and the adherence of patients who share it with you",
};

const PASSWORD_RULE = /^(?=.*[A-Za-z])(?=.*\d).{8,}$/;

export default function Signup() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  // The selectable roles come from the server. The administrator is not among them.
  const roles = useQuery({ queryKey: ["signup-roles"], queryFn: () => api<RoleOption[]>("/auth/roles") });
  const [form, setForm] = useState({ name: "", email: "", password: "", confirm_password: "", role: "" });
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const cards = useRef<Array<HTMLButtonElement | null>>([]);
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm({ ...form, [key]: e.target.value });
  const blur = (key: string) => () => setTouched({ ...touched, [key]: true });
  const show = (key: string) => submitted || touched[key];

  const problems = {
    role: !form.role ? "Choose the role you work in." : null,
    name: !form.name.trim() ? "Enter your full name." : null,
    email: !/^\S+@\S+\.\S+$/.test(form.email) ? "Enter a valid email address." : null,
    password: !PASSWORD_RULE.test(form.password) ? "Use at least 8 characters, with a letter and a number." : null,
    confirm_password:
      form.confirm_password !== form.password || !form.confirm_password ? "The two passwords do not match." : null,
  };
  const invalid = Object.values(problems).some(Boolean);
  const options = roles.data ?? [];

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitted(true);
    if (invalid) return;
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

  // Arrow keys move through the role cards, as in any radio group.
  function onRoleKey(event: KeyboardEvent, index: number) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!step || !options.length) return;
    event.preventDefault();
    const next = (index + step + options.length) % options.length;
    setForm({ ...form, role: options[next].role });
    cards.current[next]?.focus();
  }

  const selectedIndex = options.findIndex((o) => o.role === form.role);

  return (
    <AuthLayout
      wide
      title="Create your account"
      subtitle="Choose the role you work in. It decides what you can see and do, and it cannot be changed later."
      footer={
        <>
          Already registered?{" "}
          <Link to="/login" className="font-semibold text-primary-ink underline-offset-4 hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-8" noValidate>
        <fieldset>
          <legend className="mb-3 flex items-baseline gap-1 text-sm font-medium text-ink">
            Role <span className="text-bad" aria-hidden>*</span>
          </legend>
          {roles.isLoading ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-[92px] rounded-xl" />
              ))}
            </div>
          ) : roles.error ? (
            <ErrorNote error={roles.error} />
          ) : (
            <div role="radiogroup" aria-label="Role" className="grid gap-3 sm:grid-cols-2">
              {options.map((option, i) => {
                const Icon = ROLE_ICON[option.role] ?? UserRound;
                const selected = form.role === option.role;
                return (
                  <button
                    type="button"
                    key={option.role}
                    ref={(el) => {
                      cards.current[i] = el;
                    }}
                    role="radio"
                    aria-checked={selected}
                    tabIndex={selected || (selectedIndex === -1 && i === 0) ? 0 : -1}
                    onClick={() => setForm({ ...form, role: option.role })}
                    onKeyDown={(e) => onRoleKey(e, i)}
                    className={cx(
                      "flex min-h-[92px] items-start gap-3.5 rounded-xl border bg-surface p-4 text-left transition-[border-color,box-shadow,background-color]",
                      selected
                        ? "border-primary bg-primary-soft/60 shadow-[0_0_0_3px_color-mix(in_srgb,var(--primary)_18%,transparent)]"
                        : "border-line hover:border-line-strong hover:bg-subtle/50",
                    )}
                  >
                    <span
                      className={cx(
                        "grid h-10 w-10 shrink-0 place-items-center rounded-[10px] transition-colors",
                        selected ? "bg-primary text-on-primary" : "bg-subtle text-ink-muted",
                      )}
                    >
                      {selected ? <Check className="h-5 w-5" aria-hidden /> : <Icon className="h-5 w-5" aria-hidden />}
                    </span>
                    <span className="min-w-0">
                      <span className="block text-[15px] font-semibold text-ink">{option.label}</span>
                      <span className="mt-0.5 block text-[13px] leading-5 text-ink-muted">
                        {ROLE_SCOPE[option.role] ?? option.description}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          {show("role") && problems.role && <p className="mt-2 text-[13px] text-bad">{problems.role}</p>}
          <p className="mt-3 text-[13px] text-ink-subtle">
            Administrator accounts are not available through sign-up.
          </p>
        </fieldset>

        <div className="grid gap-5 sm:grid-cols-2">
          <TextField
            label="Full name"
            autoComplete="name"
            value={form.name}
            onChange={set("name")}
            onBlur={blur("name")}
            error={show("name") && problems.name}
            required
          />
          <TextField
            label="Email"
            type="email"
            inputMode="email"
            autoComplete="email"
            value={form.email}
            onChange={set("email")}
            onBlur={blur("email")}
            required
            error={show("email") && problems.email}
            hint="We send a verification code to this address."
          />
          <PasswordField
            label="Password"
            autoComplete="new-password"
            value={form.password}
            onChange={set("password")}
            onBlur={blur("password")}
            required
            error={show("password") && problems.password}
            hint="At least 8 characters, with a letter and a number."
          />
          <PasswordField
            label="Confirm password"
            autoComplete="new-password"
            value={form.confirm_password}
            onChange={set("confirm_password")}
            onBlur={blur("confirm_password")}
            required
            error={(show("confirm_password") || (form.confirm_password && form.confirm_password !== form.password)) && problems.confirm_password}
          />
        </div>

        <ErrorNote error={error} />
        <Button type="submit" variant="primary" size="lg" busy={busy} className="w-full sm:w-auto">
          {busy ? "Creating account" : "Create account"}
        </Button>
      </form>
    </AuthLayout>
  );
}
