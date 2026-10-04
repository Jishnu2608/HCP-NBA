// The personal details both sign-up paths ask for: patient self-registration and accepting
// a professional invitation. Checks here are for the person filling in the form; the server
// validates every field again and decides everything that matters (role, age, verification).
import { useState } from "react";
import { ApiError } from "../api";
import { PasswordField, TextField } from "../ui";

export interface AccountValues {
  name: string;
  email: string;
  date_of_birth: string;
  password: string;
  confirm_password: string;
}

export const EMPTY_ACCOUNT: AccountValues = {
  name: "",
  email: "",
  date_of_birth: "",
  password: "",
  confirm_password: "",
};

const PASSWORD_RULE = /^(?=.*[A-Za-z])(?=.*\d).{10,}$/;

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function accountProblems(v: AccountValues, emailFixed: boolean) {
  return {
    name: !v.name.trim() ? "Enter your full name." : null,
    email: emailFixed || /^\S+@\S+\.\S+$/.test(v.email) ? null : "Enter a valid email address.",
    date_of_birth: !v.date_of_birth
      ? "Enter your date of birth."
      : v.date_of_birth > today()
        ? "Date of birth cannot be in the future."
        : null,
    password: !PASSWORD_RULE.test(v.password) ? "Use at least 10 characters, with a letter and a number." : null,
    confirm_password:
      v.confirm_password !== v.password || !v.confirm_password ? "The two passwords do not match." : null,
  };
}

/** Server error codes that belong next to one field rather than above the button. */
const FIELD_OF: Record<string, keyof AccountValues> = Object.fromEntries([
  ["invalid_name", "name"],
  ["invalid_email", "email"],
  ["invalid_dob", "date_of_birth"],
  ["age_requirement", "date_of_birth"],
  ["weak_password", "password"],
  ["password_mismatch", "confirm_password"],
]);

export function fieldError(error: unknown): { field: keyof AccountValues; message: string } | null {
  if (error instanceof ApiError && error.code && FIELD_OF[error.code]) {
    return { field: FIELD_OF[error.code], message: error.message };
  }
  return null;
}

export function useAccountForm(initial: Partial<AccountValues> = {}) {
  const [values, setValues] = useState<AccountValues>({ ...EMPTY_ACCOUNT, ...initial });
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [submitted, setSubmitted] = useState(false);
  return { values, setValues, touched, setTouched, submitted, setSubmitted };
}

export function AccountFields({
  form,
  emailFixed = false,
  serverError,
}: {
  form: ReturnType<typeof useAccountForm>;
  /** The email comes from an invitation and cannot be edited. */
  emailFixed?: boolean;
  serverError?: unknown;
}) {
  const { values, setValues, touched, setTouched, submitted } = form;
  const problems = accountProblems(values, emailFixed);
  const server = fieldError(serverError);
  const set = (key: keyof AccountValues) => (e: { target: { value: string } }) =>
    setValues({ ...values, [key]: e.target.value });
  const blur = (key: string) => () => setTouched({ ...touched, [key]: true });
  const err = (key: keyof AccountValues) =>
    (submitted || touched[key] ? problems[key] : null) ?? (server?.field === key ? server.message : null);

  return (
    <div className="grid gap-5 sm:grid-cols-2">
      <TextField
        label="Full name"
        autoComplete="name"
        value={values.name}
        onChange={set("name")}
        onBlur={blur("name")}
        error={err("name")}
        required
      />
      <TextField
        label="Date of birth"
        type="date"
        autoComplete="bday"
        max={today()}
        min="1900-01-01"
        value={values.date_of_birth}
        onChange={set("date_of_birth")}
        onBlur={blur("date_of_birth")}
        error={err("date_of_birth")}
        hint="Used to confirm eligibility. It is not shown to other users."
        required
      />
      <TextField
        label="Email"
        type="email"
        inputMode="email"
        autoComplete="email"
        value={values.email}
        onChange={set("email")}
        onBlur={blur("email")}
        readOnly={emailFixed}
        aria-readonly={emailFixed || undefined}
        className={emailFixed ? "[&_input]:bg-subtle [&_input]:text-ink-muted" : undefined}
        required
        error={err("email")}
        hint={
          emailFixed
            ? "Set by your invitation. The verification code is sent here."
            : "We send a verification code to this address."
        }
      />
      <div className="hidden sm:block" aria-hidden />
      <PasswordField
        label="Password"
        autoComplete="new-password"
        value={values.password}
        onChange={set("password")}
        onBlur={blur("password")}
        required
        error={err("password")}
        hint="At least 10 characters, with a letter and a number."
      />
      <PasswordField
        label="Confirm password"
        autoComplete="new-password"
        value={values.confirm_password}
        onChange={set("confirm_password")}
        onBlur={blur("confirm_password")}
        required
        error={
          (submitted || touched.confirm_password || (values.confirm_password && values.confirm_password !== values.password)
            ? problems.confirm_password
            : null) ?? (server?.field === "confirm_password" ? server.message : null)
        }
      />
    </div>
  );
}
