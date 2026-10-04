// Legal documents, residence fields, agreement checkboxes and footer links.
//
// The documents come from the server (versioned; placeholders the operator has not filled
// read "[To be confirmed]"). Checkboxes here are never pre-ticked, and the server refuses a
// sign-up whose required boxes were not ticked: these components are presentation only.
import { useQuery } from "@tanstack/react-query";
import { FileWarning, Info, Scale } from "lucide-react";
import { useId } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "./api";
import { FormField, cx, fmtDate } from "./ui";

export const LEGAL_LINKS = [
  { kind: "privacy", label: "Privacy Policy" },
  { kind: "terms", label: "Terms & Conditions" },
  { kind: "cookies", label: "Cookie Policy" },
] as const;

export const legalPath = (kind: string) => `/legal/${kind}`;

/** Footer row of links to the legal documents, used on every page. */
export function LegalLinks({ className, light = false }: { className?: string; light?: boolean }) {
  return (
    <nav aria-label="Legal" className={cx("flex flex-wrap gap-x-4 gap-y-1 text-[13px]", className)}>
      {LEGAL_LINKS.map((l) => (
        <Link
          key={l.kind}
          to={legalPath(l.kind)}
          className={cx(
            "underline-offset-4 hover:underline",
            light ? "text-nav-ink-muted hover:text-white" : "text-ink-subtle hover:text-ink",
          )}
        >
          {l.label}
        </Link>
      ))}
    </nav>
  );
}

/* ------------------------------------------------------------------ residence */

interface Jurisdictions {
  countries: Array<{ code: string; name: string }>;
  us_states: Array<{ code: string; name: string }>;
}

export function useJurisdictions() {
  return useQuery({
    queryKey: ["jurisdictions"],
    queryFn: () => api<Jurisdictions>("/legal/jurisdictions"),
    staleTime: Infinity,
  });
}

const selectClass =
  "block h-11 w-full appearance-none rounded-lg border border-line-strong bg-surface px-3 text-[15px] text-ink shadow-card " +
  "focus:border-primary focus:outline-none focus:ring-[3px] focus:ring-primary/20 aria-[invalid=true]:border-bad";

export function ResidenceFields({
  country,
  region,
  onChange,
  errors,
}: {
  country: string;
  region: string | null;
  onChange: (country: string, region: string | null) => void;
  errors: { country?: string | null; region?: string | null };
}) {
  const options = useJurisdictions();
  const countryId = useId();
  const regionId = useId();
  return (
    <>
      <FormField
        label="Country of residence"
        htmlFor={countryId}
        required
        error={errors.country}
        hint="Decides which age and privacy rules apply. We do not ask for your address."
      >
        <select
          id={countryId}
          className={selectClass}
          value={country}
          aria-invalid={errors.country ? true : undefined}
          onChange={(e) => onChange(e.target.value, e.target.value === "US" ? region : null)}
        >
          <option value="">Choose a country</option>
          {(options.data?.countries ?? []).map((c) => (
            <option key={c.code} value={c.code}>
              {c.name}
            </option>
          ))}
        </select>
      </FormField>
      {country === "US" ? (
        <FormField label="State" htmlFor={regionId} required error={errors.region}>
          <select
            id={regionId}
            className={selectClass}
            value={region ?? ""}
            aria-invalid={errors.region ? true : undefined}
            onChange={(e) => onChange(country, e.target.value || null)}
          >
            <option value="">Choose a state</option>
            {(options.data?.us_states ?? []).map((s) => (
              <option key={s.code} value={s.code}>
                {s.name}
              </option>
            ))}
          </select>
        </FormField>
      ) : (
        <div className="hidden sm:block" aria-hidden />
      )}
    </>
  );
}

/* ------------------------------------------------------------------ agreements */

export const HEALTH_STATEMENT =
  "I explicitly consent to the processing of my health information (medications, prescription fills, adherence, " +
  "and the outreach and recommendations based on them) to provide adherence support, as described in the Privacy " +
  "Policy. I can withdraw this consent at any time in Data & privacy.";

export function Checkbox({
  checked,
  onChange,
  children,
  error,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  children: ReactNode;
  error?: string | null;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="flex cursor-pointer items-start gap-3 text-sm leading-6 text-ink">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          className="mt-1 h-[18px] w-[18px] shrink-0 cursor-pointer rounded border-line-strong accent-[var(--primary)]"
        />
        <span className="min-w-0">{children}</span>
      </label>
      {error && (
        <p id={`${id}-error`} className="ml-[30px] mt-1 text-[13px] text-bad">
          {error}
        </p>
      )}
    </div>
  );
}

const docLink = (kind: string, label: string) => (
  <a
    href={legalPath(kind)}
    target="_blank"
    rel="noopener"
    className="font-semibold text-primary-ink underline underline-offset-2"
  >
    {label}
  </a>
);

/** The mandatory acknowledgement and, for patients, the separate explicit consent. */
export function Agreements({
  terms,
  health,
  onTerms,
  onHealth,
  errors,
}: {
  terms: boolean;
  health?: boolean;
  onTerms: (v: boolean) => void;
  onHealth?: (v: boolean) => void;
  errors: { terms?: string | null; health?: string | null };
}) {
  return (
    <fieldset className="space-y-4 rounded-xl border border-line bg-subtle/50 p-4">
      <legend className="px-1 text-sm font-semibold text-ink">Agreements</legend>
      <Checkbox checked={terms} onChange={onTerms} error={errors.terms}>
        I have read and agree to the {docLink("terms", "Terms & Conditions")} and acknowledge the{" "}
        {docLink("privacy", "Privacy Policy")}. <span className="text-bad">*</span>
      </Checkbox>
      {onHealth && (
        <Checkbox checked={Boolean(health)} onChange={onHealth} error={errors.health}>
          {HEALTH_STATEMENT} <span className="text-bad">*</span>
        </Checkbox>
      )}
      <p className="text-[13px] leading-5 text-ink-subtle">
        These are recorded with the version you agreed to. Withdrawing a consent later does not affect processing that
        happened before.
      </p>
    </fieldset>
  );
}

/* ------------------------------------------------------------------ document rendering */

type Block =
  | { p: string }
  | { list: string[] }
  | { table: { head: string[]; rows: string[][] } }
  | { note: string }
  | { review: string };

export interface LegalDocumentData {
  kind: string;
  title: string;
  version: string;
  current_version: string;
  is_current: boolean;
  effective_date: string;
  last_updated: string;
  draft: boolean;
  unconfirmed: string[];
  summary: string;
  sections: Array<{ id: string; heading: string; blocks: Block[] }>;
}

function Text({ children }: { children: string }) {
  // Placeholders the operator has not filled are highlighted, never hidden.
  const parts = children.split(/(\[To be confirmed\])/g);
  return (
    <>
      {parts.map((part, i) =>
        part === "[To be confirmed]" ? (
          <mark key={i} className="rounded bg-warn-soft px-1 font-medium text-warn">
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}

function BlockView({ block }: { block: Block }) {
  if ("p" in block) return <p className="text-[15px] leading-7 text-ink-muted"><Text>{block.p}</Text></p>;
  if ("list" in block)
    return (
      <ul className="list-disc space-y-1.5 pl-5 text-[15px] leading-7 text-ink-muted marker:text-ink-subtle">
        {block.list.map((item, i) => (
          <li key={i}>
            <Text>{item}</Text>
          </li>
        ))}
      </ul>
    );
  if ("table" in block)
    return (
      <div className="overflow-x-auto rounded-lg border border-line">
        <table className="w-full min-w-[480px] border-collapse text-left text-sm">
          <thead className="bg-subtle text-ink">
            <tr>
              {block.table.head.map((h) => (
                <th key={h} scope="col" className="px-3 py-2.5 font-semibold">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.table.rows.map((row, i) => (
              <tr key={i} className="border-t border-line align-top">
                {row.map((cell, j) => (
                  <td key={j} className={cx("px-3 py-2.5 leading-6", j === 0 ? "font-medium text-ink" : "text-ink-muted")}>
                    <Text>{cell}</Text>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  if ("note" in block)
    return (
      <div className="flex gap-3 rounded-lg border border-info-line bg-info-soft p-4 text-[15px] leading-7 text-ink">
        <Info className="mt-1 h-4 w-4 shrink-0 text-info" aria-hidden />
        <p>
          <Text>{block.note}</Text>
        </p>
      </div>
    );
  return (
    <div className="flex gap-3 rounded-lg border border-warn-line bg-warn-soft p-3 text-sm leading-6 text-ink">
      <Scale className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
      <p>
        <span className="font-semibold text-warn">Pending legal review: </span>
        {block.review}
      </p>
    </div>
  );
}

export function DraftBanner() {
  return (
    <div role="note" className="flex gap-3 rounded-xl border border-warn-line bg-warn-soft p-4 text-sm leading-6 text-ink">
      <FileWarning className="mt-0.5 h-5 w-5 shrink-0 text-warn" aria-hidden />
      <p>
        <span className="font-semibold text-warn">Draft — pending legal review.</span> This document was prepared for a
        proof-of-concept and has not been reviewed by legal or privacy counsel. Details marked{" "}
        <mark className="rounded bg-surface px-1 font-medium text-warn">[To be confirmed]</mark> have not been supplied
        yet.
      </p>
    </div>
  );
}

export function LegalDocumentView({ doc }: { doc: LegalDocumentData }) {
  return (
    <article className="grid gap-10 lg:grid-cols-[14rem_minmax(0,1fr)]">
      <nav aria-label="Contents" className="hidden lg:block">
        <div className="sticky top-6">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-subtle">Contents</div>
          <ol className="space-y-1.5 text-sm">
            {doc.sections.map((s) => (
              <li key={s.id}>
                <a href={`#${s.id}`} className="block leading-5 text-ink-muted hover:text-primary-ink">
                  {s.heading}
                </a>
              </li>
            ))}
          </ol>
        </div>
      </nav>
      <div className="min-w-0 max-w-3xl space-y-8">
        <header className="space-y-4">
          <h1 className="text-[30px] font-semibold leading-tight tracking-[-0.015em] text-ink sm:text-[34px]">
            {doc.title}
          </h1>
          <dl className="flex flex-wrap gap-x-6 gap-y-1 text-[13px] text-ink-subtle">
            <div>
              <dt className="inline">Version </dt>
              <dd className="inline font-semibold text-ink">{doc.version}</dd>
            </div>
            <div>
              <dt className="inline">Effective </dt>
              <dd className="inline font-semibold text-ink">{fmtDate(doc.effective_date)}</dd>
            </div>
            <div>
              <dt className="inline">Last updated </dt>
              <dd className="inline font-semibold text-ink">{fmtDate(doc.last_updated)}</dd>
            </div>
          </dl>
          {!doc.is_current && (
            <p className="text-sm text-ink-muted">
              This is an earlier version. <Link to={legalPath(doc.kind)} className="font-semibold text-primary-ink underline">Read the current version ({doc.current_version})</Link>.
            </p>
          )}
          {doc.draft && <DraftBanner />}
          <p className="text-[17px] leading-8 text-ink">{doc.summary}</p>
        </header>
        <details className="rounded-lg border border-line p-3 lg:hidden">
          <summary className="cursor-pointer text-sm font-semibold text-ink">Contents</summary>
          <ol className="mt-2 space-y-1.5 text-sm">
            {doc.sections.map((s) => (
              <li key={s.id}>
                <a href={`#${s.id}`} className="text-ink-muted hover:text-primary-ink">
                  {s.heading}
                </a>
              </li>
            ))}
          </ol>
        </details>
        {doc.sections.map((s) => (
          <section key={s.id} id={s.id} className="scroll-mt-6 space-y-4">
            <h2 className="text-[20px] font-semibold leading-snug text-ink">{s.heading}</h2>
            {s.blocks.map((b, i) => (
              <BlockView key={i} block={b} />
            ))}
          </section>
        ))}
      </div>
    </article>
  );
}
