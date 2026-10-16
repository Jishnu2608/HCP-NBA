// Case Brief on Patient 360: the first operation on the shared AI layer (backend: app/ai).
//
// The server returns the brief to show (the deterministic template, or model wording that
// passed every check in prototype mode), the sources it cites, the original notes for this
// reader, and the latest generation's state. This panel only displays it and lets the
// care manager ask for a new generation; the server decides everything else.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { api, post } from "../api";
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
import { BentoCard } from "../layout";
import { ErrorNote, ErrorState, LoadingRows } from "../ui";

const POLL_MS = 2500;

export function caseBriefKey(patientId: string) {
  return ["ai", "case_brief", patientId];
}

export function CaseBriefPanel({ patientId, id }: { patientId: string; id?: string }) {
  const client = useQueryClient();
  const key = caseBriefKey(patientId);
  const brief = useQuery({
    queryKey: key,
    queryFn: () => api(`/ai/case_brief/patients/${encodeURIComponent(patientId)}`),
    // Poll only while a generation is queued or running.
    refetchInterval: (q) => ((q.state.data as Json)?.generation?.active ? POLL_MS : false),
  });
  const generate = useMutation({
    mutationFn: () => post(`/ai/case_brief/patients/${encodeURIComponent(patientId)}/generations`),
    onSettled: () => void client.invalidateQueries({ queryKey: key }),
  });

  const data: Json = brief.data;
  const generation: Generation | null = data?.generation ?? null;
  const action =
    data?.can_generate && !brief.isLoading ? (
      <GenerateButton
        active={Boolean(generation?.active)}
        busy={generate.isPending}
        hasResult={Boolean(generation)}
        onClick={() => generate.mutate()}
      />
    ) : undefined;

  return (
    <BentoCard
      id={id}
      span="full"
      icon={<Sparkles />}
      title="Case brief"
      description="A short orientation built from the record. Check it against the sources before acting."
      action={action}
    >
      {brief.isLoading ? (
        <LoadingRows rows={4} label="Loading the case brief" />
      ) : brief.error ? (
        <ErrorState error={brief.error} title="The case brief could not be loaded" retry={() => void brief.refetch()} />
      ) : (
        <CaseBriefBody data={data} generation={generation} mutationError={generate.error} />
      )}
    </BentoCard>
  );
}

function CaseBriefBody({ data, generation, mutationError }: { data: Json; generation: Generation | null; mutationError: unknown }) {
  const sources = sourcesById(data.sources);
  const result = data.result as {
    summary: Statement;
    suggested_focus: Statement;
    key_points: Statement[];
    not_recorded: string[];
  };
  const notes = (data.notes ?? []) as ReaderNote[];
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <GenerationStatus generation={generation} mode={data.mode} />
        <ErrorNote error={mutationError} />
      </div>
      {data.review_required && <ReviewNotice />}

      <div className="grid gap-3 md:grid-cols-2">
        <StatementBlock label="Summary" statement={result.summary} sources={sources} />
        <StatementBlock label="Suggested focus" statement={result.suggested_focus} sources={sources} />
      </div>

      <section aria-label="Key points from the record" className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">Key points from the record</h3>
        <ul className="divide-y divide-line rounded-lg border border-line">
          {result.key_points.map((kp, i) => (
            <li key={i} className="flex flex-wrap items-start justify-between gap-2 px-4 py-2.5">
              <span className="min-w-0 text-sm leading-6 text-ink">{kp.text}</span>
              <SourceChips ids={kp.citations} sources={sources} />
            </li>
          ))}
        </ul>
        {result.not_recorded.length > 0 && (
          <p className="text-[13px] text-ink-subtle">Not recorded: {result.not_recorded.join(" ")}</p>
        )}
      </section>

      <section aria-label="Original notes" className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">Original notes</h3>
        <p className="text-[13px] text-ink-subtle">
          Shown exactly as written. Notes are never sent to the model and do not change the brief.
        </p>
        <UntrustedNotes notes={notes} />
      </section>

      <GenerationMeta generation={generation} />
    </div>
  );
}
