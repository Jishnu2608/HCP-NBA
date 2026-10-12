import { GitMerge, HeartPulse, ShieldCheck } from "lucide-react";
import type { Json } from "../api";
import { Badge, Meter, pct } from "../ui";
import { BentoCard } from "../layout";

/** HCP 360: how this HCP's patients are getting on with their medicines, as a de-identified
 *  group of consenting patients only (GET /api/hcps/:id → panel_adherence). Never a patient,
 *  never a group below the minimum size. */
export function PanelAdherenceCard({ panel }: { panel: Json }) {
  const rows: Json[] = panel.measures;
  const shown = rows.filter((r) => !r.suppressed);
  return (
    <BentoCard
      span="wide"
      icon={<HeartPulse />}
      title="Patient adherence context"
      description={`De-identified. ${panel.basis} Minimum group: ${panel.min_cohort} patients.`}
      note={
        <span className="inline-flex items-start gap-1.5">
          <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          No patient is identifiable here. Adherence-support material for a measure below the {pct(panel.target)} target is
          favoured for this HCP; every safeguard still applies.
        </span>
      }
    >
      {shown.length === 0 ? (
        <p className="text-sm text-ink-muted">
          No group is large enough to show: fewer than {panel.min_cohort} consenting patients per measure for this HCP or their
          state.
        </p>
      ) : null}
      <ul className="grid gap-x-8 gap-y-5 md:grid-cols-3">
        {rows.map((r) => (
          <li key={r.measure} className="min-w-0">
            <div className="text-[13px] font-semibold text-ink-muted">{r.label}</div>
            {r.suppressed ? (
              <p className="mt-2 text-sm text-ink-subtle">Not shown: group too small</p>
            ) : (
              <>
                <div className="mt-1 flex items-baseline gap-2">
                  <span className="tabular text-2xl font-semibold text-ink">{pct(r.adherent_share)}</span>
                  <span className="text-[13px] text-ink-subtle">at PDC {pct(panel.pdc_threshold)}+</span>
                </div>
                <Meter
                  value={r.adherent_share}
                  tone={r.below_target ? "warn" : "ok"}
                  className="mt-2"
                  label={`${r.label}: share at PDC ${pct(panel.pdc_threshold)} or more`}
                />
                <p className="mt-2 text-[13px] text-ink-subtle">
                  {r.patients} consenting {r.scope} · {pct(r.in_gap_share)} out of medication now
                </p>
                {r.below_target && (
                  <Badge tone="warn" className="mt-2">
                    Below {pct(panel.target)} target
                  </Badge>
                )}
              </>
            )}
          </li>
        ))}
      </ul>
    </BentoCard>
  );
}

/** HCP 360: upstream records resolved to this profile (synthetic identity-resolution feeds). */
export function SourcesCard({ sources }: { sources: Json }) {
  return (
    <BentoCard
      span="narrow"
      icon={<GitMerge />}
      title="Data sources"
      description="Synthetic upstream records matched to this profile by fixed rules."
    >
      {sources.registered_directly ? (
        <p className="text-sm text-ink-muted">Registered directly through an invitation: no upstream records.</p>
      ) : (
        <ul className="space-y-3">
          {sources.merged.map((m: Json) => (
            <li key={m.source_key} className="min-w-0 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-ink">{m.source_label}</span>
                <Badge tone="ok">Merged · {pct(m.confidence)}</Badge>
              </div>
              <div className="mt-0.5 text-[13px] text-ink-subtle">
                {m.source_key} · “{m.name_in_source}” · {m.rule_label}
              </div>
            </li>
          ))}
          {sources.review.map((m: Json) => (
            <li key={m.source_key} className="min-w-0 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-ink">{m.source_label}</span>
                <Badge tone="warn">{m.status === "ambiguous" ? "Ambiguous" : "Held for review"}</Badge>
              </div>
              <div className="mt-0.5 text-[13px] text-ink-subtle">
                {m.source_key} · “{m.name_in_source}” · {m.rule_label}
                {m.other_candidates > 0 ? ` · fits ${m.other_candidates} other profile${m.other_candidates > 1 ? "s" : ""}` : ""}.
                Not merged automatically.
              </div>
            </li>
          ))}
          {sources.merged.length + sources.review.length === 0 && (
            <li className="text-sm text-ink-muted">No upstream record matched this profile.</li>
          )}
        </ul>
      )}
    </BentoCard>
  );
}
