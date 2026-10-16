// Shared pieces for AI-assisted results (backend: app/ai).
//
// Display only. What a person may read, whether a model runs and whether its wording may be
// shown are decided on the server; nothing here grants or hides access. These components keep
// three things visibly apart: facts from the record, AI-generated wording that a person must
// review, and original notes, which are shown word for word under one fixed label and never
// mixed into generated text.

import { Bot, FileText, Loader2, Quote, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import type { Json } from "./api";
import { Alert, Badge, Button, cx, fmtDate, fmtDateTime } from "./ui";

export const UNTRUSTED_NOTE_LABEL = "Untrusted; review for clinical content.";

export type Statement = { text: string; citations: string[]; origin: "record" | "template" | "model" };
export type Source = { id: string; label: string; text: string; recorded_at: string | null; model_visible: boolean };
export type ReaderNote = { label: string; text: string; source: string; source_id: string; recorded_at: string | null };
export type Generation = {
  id: number;
  status: "queued" | "running" | "completed" | "fallback" | "failed";
  mode: string;
  failure: string | null;
  gate_reasons: string[];
  provider: string | null;
  model: string | null;
  operation_version: string;
  prompt_version: string;
  requested_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  stale: boolean;
  outdated: boolean;
  shown: boolean;
  active: boolean;
};

/** Why the template stands, in words a care manager can act on. Codes come from the server. */
export const FAILURE_TEXT: Record<string, string> = {
  provider_disabled: "AI generation is switched off on this server.",
  provider_unavailable: "The local model did not answer in time or is not running.",
  data_policy: "The request was stopped before reaching the model by a data-protection check.",
  schema_invalid: "The model's answer was not in the required form.",
  gate_rejected: "The model's wording failed a safety check.",
  access_lost: "Your access to this patient changed before the request ran.",
  interrupted: "The request stopped before it finished.",
  internal: "The request could not be completed.",
};

/** One short label per check that can reject model wording. */
export const CHECK_TEXT: Record<string, string> = {
  schema: "answer format",
  invalid_citation: "citation of an unknown source",
  miscited: "fact not in the cited source",
  fabricated_date: "date not in the record",
  fabricated_number: "number not in the record",
  fabricated_id: "identifier not in the record",
  fabricated_drug: "medicine not in the record",
  fabricated_condition: "condition not in the record",
  fabricated_recommendation: "recommendation not in the record",
  "clinical_claim:dosing": "dosing wording",
  "clinical_claim:dosing_advice": "medication advice",
  "clinical_claim:clinical": "clinical claim",
  unsafe_medication: "medication or symptom wording",
  consent_claim: "statement about consent",
  action_claim: "claim that something was done",
  "leak:contact": "contact details",
  "leak:link": "link",
  "leak:identifier": "name or identifier",
  "leak:free_text": "text copied from a note",
  too_long: "length",
};

/** The generation's state, as one badge and one sentence. */
export function GenerationStatus({ generation, mode }: { generation: Generation | null; mode: string }) {
  if (!generation) {
    return (
      <p className="text-sm text-ink-subtle">
        {mode === "template" ? "AI wording is switched off here." : "No AI wording requested yet."}
      </p>
    );
  }
  const g = generation;
  if (g.active) {
    return (
      <div className="flex items-center gap-2 text-sm text-ink-muted" role="status" aria-live="polite">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        {g.status === "queued" ? "Waiting for the local model…" : "Generating with the local model…"}
      </div>
    );
  }
  let badge: ReactNode;
  let text: string;
  if (g.status === "completed" && g.shown) {
    badge = <Badge tone="info" icon={<Bot className="h-3.5 w-3.5" aria-hidden />}>AI wording shown</Badge>;
    text = "Every safety check passed. Review the wording against the sources before acting.";
  } else if (g.status === "completed") {
    badge = <Badge tone="neutral">Template shown</Badge>;
    text = g.stale
      ? "The record has changed since this wording was generated, so the current template is shown."
      : g.outdated
        ? "This wording came from an earlier version of the brief, so the current template is shown."
        : "Evaluation mode: the AI wording is checked and recorded but not shown.";
  } else if (g.status === "fallback") {
    badge = <Badge tone="warn">Template shown</Badge>;
    text = FAILURE_TEXT[g.failure ?? ""] ?? "The template is shown.";
  } else {
    badge = <Badge tone="bad">Not completed</Badge>;
    text = FAILURE_TEXT[g.failure ?? ""] ?? "The request could not be completed.";
  }
  const checks = g.gate_reasons.filter((r) => r !== "schema" || g.failure === "schema_invalid");
  return (
    <div className="space-y-1.5 text-sm" role="status">
      <div className="flex flex-wrap items-center gap-2">
        {badge}
        <span className="text-ink-muted">{text}</span>
      </div>
      {g.failure === "gate_rejected" && checks.length > 0 && (
        <p className="text-[13px] text-ink-subtle">
          Failed checks: {checks.map((c) => CHECK_TEXT[c] ?? c).join(", ")}.
        </p>
      )}
    </div>
  );
}

/** Where a statement came from. AI wording is never styled like a recorded fact. */
export function OriginBadge({ origin }: { origin: Statement["origin"] }) {
  if (origin === "model") {
    return (
      <Badge tone="info" icon={<Bot className="h-3.5 w-3.5" aria-hidden />}>
        AI-generated · review
      </Badge>
    );
  }
  return (
    <Badge tone="neutral" icon={<FileText className="h-3.5 w-3.5" aria-hidden />}>
      {origin === "template" ? "Template" : "From the record"}
    </Badge>
  );
}

/** Cited sources as small references; each shows the server-written fact on hover or focus. */
export function SourceChips({ ids, sources }: { ids: string[]; sources: Record<string, Source> }) {
  if (!ids.length) return null;
  // Two refill records cited together read "Refill record 1" and "Refill record 2".
  const labels = ids.map((id) => sources[id]?.label ?? id);
  const repeated = (label: string) => labels.filter((l) => l === label).length > 1;
  return (
    <span className="flex flex-wrap gap-1.5" aria-label="Sources">
      {ids.map((id) => {
        const s = sources[id];
        const label = s ? (repeated(s.label) ? `${s.label} ${id.split(":").pop()}` : s.label) : id;
        return (
          <span
            key={id}
            tabIndex={0}
            title={s ? s.text : "Unknown source"}
            className="rounded border border-line bg-sunken px-1.5 py-0.5 text-[12px] leading-5 text-ink-muted"
          >
            {label}
          </span>
        );
      })}
    </span>
  );
}

export function StatementBlock({
  label,
  statement,
  sources,
}: {
  label: string;
  statement: Statement;
  sources: Record<string, Source>;
}) {
  const generated = statement.origin === "model";
  return (
    <div
      className={cx(
        "space-y-2 rounded-lg border px-4 py-3",
        generated ? "border-info-line bg-info-soft/40 border-dashed" : "border-line",
      )}
      data-origin={statement.origin}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">{label}</span>
        <OriginBadge origin={statement.origin} />
      </div>
      <p className="text-sm leading-6 text-ink">{statement.text}</p>
      <SourceChips ids={statement.citations} sources={sources} />
    </div>
  );
}

/** Original notes for an authorised reader: verbatim, labelled, apart from generated text. */
export function UntrustedNotes({ notes }: { notes: ReaderNote[] }) {
  if (!notes.length) return <p className="text-sm text-ink-subtle">No notes on record.</p>;
  return (
    <ul className="space-y-2.5" aria-label="Original notes">
      {notes.map((n) => (
        <li key={`${n.source}:${n.source_id}`} className="rounded-lg border border-warn-line bg-warn-soft/30 px-4 py-3">
          <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
            <Badge tone="warn" icon={<Quote className="h-3.5 w-3.5" aria-hidden />}>
              {n.label}
            </Badge>
            <span className="text-[12px] text-ink-subtle">
              {noteSource(n.source)}
              {n.recorded_at ? ` · ${fmtDate(n.recorded_at)}` : ""}
            </span>
          </div>
          {/* Plain text: React escapes it, so a note can never become markup or a link. */}
          <p className="whitespace-pre-wrap break-words text-sm leading-6 text-ink">{n.text}</p>
        </li>
      ))}
    </ul>
  );
}

function noteSource(source: string) {
  if (source.endsWith("health.notes")) return "Care note";
  if (source.endsWith("outcome_reason")) return "Outreach reason";
  return "Outreach note";
}

/** Model, versions and timing, for people who want to know what produced the wording. */
export function GenerationMeta({ generation }: { generation: Generation | null }) {
  if (!generation || generation.active) return null;
  const g = generation;
  const rows: Array<[string, ReactNode]> = [
    ["Model", g.model ? `${g.model} (${g.provider === "ollama" ? "local" : g.provider})` : "none"],
    ["Brief version", `${g.operation_version} · prompt v${g.prompt_version}`],
    ["Requested", fmtDateTime(g.requested_at)],
    ["Finished", g.finished_at ? fmtDateTime(g.finished_at) : "—"],
    ["Took", g.duration_ms !== null ? `${(g.duration_ms / 1000).toFixed(1)} s` : "—"],
  ];
  return (
    <details className="text-[13px] text-ink-subtle">
      <summary className="cursor-pointer select-none">Generation details</summary>
      <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt>{k}</dt>
            <dd className="text-ink-muted">{v}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

export function ReviewNotice() {
  return (
    <Alert tone="info" icon={<Bot className="h-5 w-5" aria-hidden />} title="AI-generated wording needs your review">
      Prototype on synthetic data. The summary and focus were written by a local model and passed the server's
      checks; they can still be worded imperfectly. The key points below come from the record.
    </Alert>
  );
}

export function GenerateButton({
  active,
  busy,
  hasResult,
  onClick,
}: {
  active: boolean;
  busy: boolean;
  hasResult: boolean;
  onClick: () => void;
}) {
  return (
    <Button size="sm" variant="secondary" busy={busy || active} onClick={onClick}>
      {!busy && !active && <RefreshCw className="h-4 w-4" aria-hidden />}
      {hasResult ? "Generate again" : "Generate AI wording"}
    </Button>
  );
}

export function sourcesById(list: Json[] | undefined): Record<string, Source> {
  return Object.fromEntries((list ?? []).map((s: Source) => [s.id, s]));
}
