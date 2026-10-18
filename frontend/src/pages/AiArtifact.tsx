// One panel for every read-only AI operation built on the shared layer (backend: app/ai,
// subject-agnostic routes /api/ai/{operation}/current and /generations): the decision trace,
// engagement brief, queue brief, compliance summary and cycle time. The server returns the
// result to show (the deterministic template by default, or model wording that passed every
// check in prototype mode), the sources it cites, server notes about the data, and the latest
// generation's state. The panel only displays it and offers the Generate action when the
// server allows one; access, scope, mode, checks and fallback are decided on the server.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, post } from "../api";
import type { Json } from "../api";
import {
  GenerateButton,
  GenerationMeta,
  GenerationStatus,
  ReviewNotice,
  SourceChips,
  StatementBlock,
  UntrustedNotes,
  sourcesById,
} from "../ai";
import type { Generation, ReaderNote, Statement } from "../ai";
import { Alert, Card, ErrorNote, ErrorState, LoadingRows } from "../ui";

const POLL_MS = 2500;

type ArtifactResult = {
  statements: Array<{ label: string; statement: Statement }>;
  key_points: Statement[];
  notes: string[];
  not_recorded: string[];
};

export function artifactKey(operation: string, subjectId?: string | null) {
  return ["ai", operation, subjectId ?? "self"];
}

export function AiArtifactPanel({
  operation,
  subjectId,
  title,
  description,
  id,
}: {
  operation: string;
  subjectId?: string | null;
  title: string;
  description: string;
  id?: string;
}) {
  const client = useQueryClient();
  const key = artifactKey(operation, subjectId);
  const query = subjectId ? `?subject_id=${encodeURIComponent(subjectId)}` : "";
  const view = useQuery({
    queryKey: key,
    queryFn: () => api(`/ai/${operation}/current${query}`),
    // Poll only while a generation is queued or running.
    refetchInterval: (q) => ((q.state.data as Json)?.generation?.active ? POLL_MS : false),
  });
  const generate = useMutation({
    mutationFn: () => post(`/ai/${operation}/generations`, subjectId ? { subject_id: subjectId } : {}),
    onSettled: () => void client.invalidateQueries({ queryKey: key }),
  });

  const data: Json = view.data;
  const generation: Generation | null = data?.generation ?? null;
  const action =
    data?.can_generate && !view.isLoading ? (
      <GenerateButton
        active={Boolean(generation?.active)}
        busy={generate.isPending}
        hasResult={Boolean(generation)}
        onClick={() => generate.mutate()}
      />
    ) : undefined;

  return (
    <Card id={id} title={title} description={description} action={action} dataAttr={`ai-${operation}`}>
      {view.isLoading ? (
        <LoadingRows rows={3} label={`Loading ${title.toLowerCase()}`} />
      ) : isUnavailable(view.error) ? (
        <Alert tone="info" title={`${title} not available here`}>
          This server uses a database without the AI features. Everything else on this page works as usual.
        </Alert>
      ) : view.error ? (
        <ErrorState error={view.error} title={`${title} could not be loaded`} retry={() => void view.refetch()} />
      ) : (
        <ArtifactBody data={data} generation={generation} mutationError={generate.error} />
      )}
    </Card>
  );
}

function isUnavailable(error: unknown) {
  return error instanceof ApiError && error.status === 503 && error.code === "ai_unavailable";
}

function ArtifactBody({ data, generation, mutationError }: { data: Json; generation: Generation | null; mutationError: unknown }) {
  const sources = sourcesById(data.sources);
  const result = data.result as ArtifactResult;
  const notes = (data.notes ?? []) as ReaderNote[];
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <GenerationStatus generation={generation} mode={data.mode} />
        <ErrorNote error={mutationError} />
      </div>
      {data.review_required && <ReviewNotice />}

      <div className={result.statements.length > 1 ? "grid gap-3 md:grid-cols-2" : "grid gap-3"}>
        {result.statements.map((s) => (
          <StatementBlock key={s.label} label={s.label} statement={s.statement} sources={sources} />
        ))}
      </div>

      {result.key_points.length > 0 && (
        <section aria-label="From the record" className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">From the record</h3>
          <ul className="divide-y divide-line rounded-lg border border-line">
            {result.key_points.map((kp, i) => (
              <li key={i} className="flex flex-wrap items-start justify-between gap-2 px-4 py-2.5">
                <span className="min-w-0 text-sm leading-6 text-ink">{kp.text}</span>
                <SourceChips ids={kp.citations} sources={sources} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {(result.notes.length > 0 || result.not_recorded.length > 0) && (
        <section aria-label="About this data" className="space-y-1">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">About this data</h3>
          <ul className="list-disc space-y-1 pl-5 text-[13px] text-ink-subtle">
            {result.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
            {result.not_recorded.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </section>
      )}

      {notes.length > 0 && (
        <section aria-label="Original notes" className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">Original notes</h3>
          <UntrustedNotes notes={notes} />
        </section>
      )}

      <GenerationMeta generation={generation} />
    </div>
  );
}
