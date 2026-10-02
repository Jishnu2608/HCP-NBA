import { Activity } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function Brand({ light = false }: { light?: boolean }) {
  return (
    <Link to="/" className="flex items-center gap-2.5">
      <span
        className={
          light
            ? "grid h-9 w-9 place-items-center rounded-xl bg-white/15 text-white"
            : "grid h-9 w-9 place-items-center rounded-xl bg-brand-600 text-white"
        }
      >
        <Activity className="h-5 w-5" />
      </span>
      <span className={light ? "text-lg font-semibold text-white" : "text-lg font-semibold text-stone-900"}>
        Next Best Action
      </span>
    </Link>
  );
}

/** Shared frame for the sign-in, sign-up and verification pages. */
export default function AuthLayout({
  title,
  subtitle,
  children,
  wide = false,
}: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div className="min-h-full bg-gradient-to-b from-brand-900 to-brand-700 px-4 py-8 sm:px-6 sm:py-12">
      <div className={wide ? "mx-auto max-w-3xl" : "mx-auto max-w-md"}>
        <Brand light />
        <div className="mt-6 rounded-2xl bg-white p-6 shadow-xl sm:p-8">
          <h1 className="text-xl font-semibold tracking-tight text-stone-900">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-stone-500">{subtitle}</p>}
          <div className="mt-6">{children}</div>
        </div>
        <p className="mt-4 text-center text-xs text-white/60">
          Synthetic demonstration data. A communication-decision tool, not a clinical one.
        </p>
      </div>
    </div>
  );
}

export function TextField({
  label,
  hint,
  ...input
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block text-xs font-medium text-stone-600">{label}</span>
      <input
        {...input}
        className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
      />
      {hint && <span className="mt-1 block text-xs text-stone-500">{hint}</span>}
    </label>
  );
}
