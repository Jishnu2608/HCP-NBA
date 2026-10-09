import {
  ArrowRight,
  BarChart3,
  CalendarClock,
  Check,
  ClipboardList,
  FileCheck2,
  Gauge,
  HeartPulse,
  ListChecks,
  MessageSquareText,
  ScrollText,
  ShieldCheck,
  Smartphone,
  Stethoscope,
  UserCheck,
  UserRound,
  Users,
} from "lucide-react";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useRef } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { CareNetwork } from "../ambient";
import { DUR, MEDIA, gsap, useGSAP, useMagnetic, useSpotlight } from "../motion";
import { ThemeToggle } from "../theme";
import { cx } from "../ui";
import { Brand } from "./AuthLayout";

// ScrollTrigger is used on this page only (it loads with the landing chunk): one trigger
// per section, revealed once, never pinned or scrubbed.
gsap.registerPlugin(ScrollTrigger);

const LOOP = [
  ["Unify", "One profile per HCP and patient: fills, interactions, consent, content."],
  ["Segment", "Adherence risk for patients; value and engagement for HCPs."],
  ["Predict", "Chance of a response and of a refill, for every option."],
  ["Gate", "Content approval, consent and contact limits. Rules, not a model."],
  ["Personalize", "Wording drafted only from approved content, then checked."],
  ["Review", "A person approves, edits or rejects. Gates run again."],
  ["Engage", "Sent on the chosen channel after a final gate check."],
  ["Learn", "Responses and refills feed the next cycle."],
];

const ROLES: Array<{ icon: typeof Users; name: string; text: string }> = [
  { icon: HeartPulse, name: "Care Manager", text: "Adherence queue and Patient 360 for assigned patients" },
  { icon: Stethoscope, name: "Medical Representative", text: "HCP queue, HCP 360 and approved content for assigned HCPs" },
  { icon: FileCheck2, name: "Compliance / MLR Reviewer", text: "Content approval, blocked recommendations, audit log" },
  { icon: Users, name: "Administrator", text: "Users, assignments, engine operations and analytics" },
  { icon: Stethoscope, name: "Healthcare Professional", text: "Own inbox and the adherence of consenting patients" },
  { icon: UserRound, name: "Patient", text: "Own medications, messages and contact preferences" },
];

function CtaLink({
  to,
  variant,
  children,
  className,
  magnetic,
}: {
  to: string;
  variant: "accent" | "quiet" | "light";
  children: ReactNode;
  className?: string;
  magnetic?: boolean;
}) {
  const ref = useRef<HTMLAnchorElement>(null);
  // The main call to action leans very slightly toward a mouse pointer (desktop only).
  useMagnetic(magnetic ? ref : { current: null });
  const style = {
    accent: "bg-accent text-on-accent shadow-card hover:bg-accent-hover",
    quiet: "border border-line-strong bg-surface text-ink hover:bg-subtle",
    light: "border border-white/25 text-white hover:bg-white/10",
  };
  return (
    <Link
      ref={ref}
      to={to}
      className={cx(
        "inline-flex min-h-12 items-center justify-center gap-2 whitespace-nowrap rounded-lg px-5 text-[15px] font-semibold transition-colors active:translate-y-px",
        style[variant],
        className,
      )}
    >
      {children}
    </Link>
  );
}

/** A static rendering of the product's core object, used as the hero visual. */
function RecommendationPreview() {
  const reasons = [
    "Days covered fell to 61%, below the 80% target",
    "No supply for 18 days since the last refill ran out",
    "Last two emails went unanswered",
    "Text-message consent on record",
  ];
  return (
    <div className="relative mx-auto w-full max-w-[460px] lg:mx-0">
      <div
        aria-hidden
        data-hero="back"
        className="absolute -right-4 top-10 hidden h-[88%] w-[92%] rotate-[2.5deg] rounded-2xl border border-line bg-sage sm:block"
      />
      <figure
        data-hero="card"
        aria-label="Example recommendation"
        className="relative overflow-hidden rounded-2xl border border-line bg-surface shadow-overlay"
      >
        <div className="flex items-center justify-between gap-3 border-b border-line bg-subtle/60 px-5 py-3">
          <div className="flex min-w-0 items-center gap-2 text-[13px] font-medium text-ink-muted">
            <span className="h-2 w-2 shrink-0 rounded-full bg-accent" aria-hidden />
            <span className="truncate">Next best action · Patient</span>
          </div>
          <span className="inline-flex items-center gap-1 rounded-md bg-bad-soft px-1.5 py-0.5 text-xs font-semibold text-bad ring-1 ring-inset ring-bad-line">
            High risk
          </span>
        </div>
        <div className="px-5 pb-5 pt-4">
          <div className="text-[22px] font-semibold tracking-[-0.01em] text-ink">Refill reminder</div>
          <dl data-hero="facts" className="mt-3 grid grid-cols-3 gap-2">
            {[
              [<Smartphone key="i" className="h-3.5 w-3.5" aria-hidden />, "Channel", "Text"],
              [<CalendarClock key="i" className="h-3.5 w-3.5" aria-hidden />, "Timing", "Today"],
              [<Gauge key="i" className="h-3.5 w-3.5" aria-hidden />, "Fill chance", "34%"],
            ].map(([icon, label, value]) => (
              <div key={String(label)} className="rounded-lg bg-subtle px-2.5 py-2">
                <dt className="flex items-center gap-1 text-[11px] font-medium text-ink-subtle">
                  {icon}
                  {label}
                </dt>
                <dd className="tabular mt-0.5 text-sm font-semibold text-ink">{value}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-4 text-[13px] font-semibold text-ink-muted">Why this action</div>
          <ul className="mt-2 space-y-1.5">
            {reasons.map((r) => (
              <li key={r} data-hero="reason" className="flex gap-2 text-[13px] leading-5 text-ink">
                <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary-ink" aria-hidden />
                {r}
              </li>
            ))}
          </ul>
          <div className="mt-4 flex flex-wrap gap-1.5">
            {["MLR approved", "Consent granted", "Within contact limit"].map((g) => (
              <span
                key={g}
                data-hero="gate"
                className="inline-flex items-center gap-1 rounded-md bg-ok-soft px-1.5 py-0.5 text-xs font-semibold text-ok ring-1 ring-inset ring-ok-line"
              >
                <ShieldCheck className="h-3 w-3" aria-hidden /> {g}
              </span>
            ))}
          </div>
          <div data-hero="action" className="mt-5 flex gap-2">
            <span className="inline-flex min-h-9 flex-1 items-center justify-center rounded-lg bg-primary text-[13px] font-semibold text-on-primary">
              Approve and send
            </span>
            <span className="inline-flex min-h-9 items-center justify-center rounded-lg border border-line-strong px-3 text-[13px] font-semibold text-ink">
              Edit
            </span>
          </div>
        </div>
      </figure>
      <figcaption className="mt-3 text-center text-xs text-ink-subtle lg:text-left">
        Illustration with synthetic values.
      </figcaption>
    </div>
  );
}

const STRIP = [
  { icon: HeartPulse, name: "Patient signal", text: "Supply running out" },
  { icon: Gauge, name: "Insight", text: "Risk and response chance" },
  { icon: ListChecks, name: "Next best action", text: "Refill reminder by text" },
  { icon: UserCheck, name: "Human review", text: "Approved by the care team" },
  { icon: Check, name: "Outcome", text: "Refill recorded, the model learns" },
];

/**
 * From signal to outcome, in five steps. When it first comes into view each step lights up
 * in turn and the connector to the next one draws. Horizontal from 640px, vertical below.
 */
function DataToAction() {
  const ref = useRef<HTMLOListElement>(null);
  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add({ wide: "(min-width: 640px)", reduce: MEDIA.reduce }, (ctx) => {
        const { wide, reduce } = ctx.conditions as { wide: boolean; reduce: boolean };
        if (reduce) return;
        const nodes = gsap.utils.toArray<HTMLElement>("[data-strip-node]");
        const glows = gsap.utils.toArray<HTMLElement>("[data-strip-glow]");
        const step = 0.22;
        const tl = gsap.timeline({ paused: true, defaults: { duration: 0.28 } });
        nodes.forEach((node, i) => {
          tl.from(node, { opacity: 0.35, y: 6 }, i * step);
          tl.fromTo(glows[i], { opacity: 0, scale: 0.8 }, { opacity: 1, scale: 1 }, i * step);
        });
        if (wide) {
          // One continuous line from the first step to the last, filling as each lights up.
          tl.fromTo(
            "[data-strip-fill]",
            { scaleX: 0 },
            { scaleX: 1, duration: step * (nodes.length - 1), ease: "none", transformOrigin: "0% 50%" },
            0.05,
          );
        } else {
          const links = gsap.utils.toArray<HTMLElement>("[data-strip-vlink]");
          links.forEach((link, i) => tl.fromTo(link, { scaleY: 0 }, { scaleY: 1, transformOrigin: "50% 0%" }, i * step + 0.12));
        }
        ScrollTrigger.create({ trigger: ref.current, start: "top 85%", once: true, onEnter: () => tl.play() });
      });
      return () => mm.revert();
    },
    { scope: ref },
  );
  return (
    <ol ref={ref} aria-label="From a patient signal to an outcome" className="relative flex flex-col gap-0 sm:flex-row sm:items-start">
      {/* Desktop: a single track behind the steps, from the centre of the first (10%) to the
          centre of the last (90%) of five equal columns, vertically through the icons. */}
      <span aria-hidden className="pointer-events-none absolute left-[10%] right-[10%] top-5 hidden h-0.5 -translate-y-1/2 rounded-full bg-line sm:block">
        <span data-strip-fill className="block h-full w-full rounded-full bg-primary-line" />
      </span>
      {STRIP.map(({ icon: Icon, name, text }, i) => (
        <li key={name} className="relative flex gap-3 sm:flex-1 sm:flex-col sm:items-center sm:gap-0 sm:text-center">
          <div className="flex flex-col items-center">
            <span data-strip-node className="relative grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-surface text-primary-ink ring-1 ring-line">
              <span data-strip-glow aria-hidden className="absolute inset-0 rounded-xl bg-primary-soft ring-2 ring-primary-line" />
              <Icon className="relative h-[18px] w-[18px]" aria-hidden />
            </span>
            {i < STRIP.length - 1 && (
              <span className="my-1 block h-6 w-0.5 rounded-full bg-line sm:hidden" aria-hidden>
                <span data-strip-vlink className="block h-full w-full rounded-full bg-primary-line" />
              </span>
            )}
          </div>
          <div className="pb-3 sm:mt-2 sm:px-2 sm:pb-0">
            <p className="text-sm font-semibold text-ink">{name}</p>
            <p className="text-[13px] leading-5 text-ink-muted">{text}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

/**
 * The hero plays once, in reading order: headline, promise, actions, then the example
 * recommendation builds itself the way a reviewer reads one (card, facts, reasons,
 * safeguards passed, decision). Under a second; nothing loops.
 */
function useHeroIntro(scope: React.RefObject<HTMLElement | null>) {
  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add(MEDIA.reduce, () => {});
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const tl = gsap.timeline({ defaults: { duration: DUR.expressive, ease: "power3.out" } });
        tl.from("[data-hero='text'] > *", { opacity: 0, y: 18, stagger: 0.08 })
          .from("[data-hero='card']", { opacity: 0, y: 24, duration: 0.5 }, 0.15)
          .from("[data-hero='back']", { opacity: 0, rotation: 0, duration: 0.6 }, 0.3)
          .from("[data-hero='facts'] > *", { opacity: 0, y: 6, stagger: 0.05, duration: 0.3 }, 0.4)
          .from("[data-hero='reason']", { opacity: 0, x: -8, stagger: 0.07, duration: 0.3 }, 0.5)
          .from("[data-hero='gate']", { opacity: 0, scale: 0.85, stagger: 0.06, duration: 0.25, ease: "back.out(2)" }, 0.75)
          .from("[data-hero='action']", { opacity: 0, y: 6, duration: 0.3 }, 0.9);
      });
      return () => mm.revert();
    },
    { scope },
  );
}

/**
 * The eight-step loop: when it scrolls into view each step's marker draws in order. While
 * it stays on screen, one step at a time is highlighted round the cycle (a single tween at
 * a time, transform only), and the highlight pauses whenever the section leaves the view.
 * Off on touch screens and under reduced motion.
 */
function useLoopMotion(scope: React.RefObject<HTMLElement | null>) {
  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add({ fine: "(pointer: fine)", reduce: MEDIA.reduce }, (ctx) => {
        const { fine, reduce } = ctx.conditions as { fine: boolean; reduce: boolean };
        if (reduce) return;
        const marks = gsap.utils.toArray<HTMLElement>("[data-loop-mark]");
        const sweeps = gsap.utils.toArray<HTMLElement>("[data-loop-sweep]");
        gsap.set(marks, { scaleX: 0, transformOrigin: "0% 50%" });
        const intro = gsap.to(marks, { scaleX: 1, duration: 0.35, stagger: 0.09, paused: true });
        let cycle: gsap.core.Timeline | null = null;
        if (fine) {
          cycle = gsap.timeline({ paused: true, repeat: -1 });
          sweeps.forEach((el) => {
            cycle!
              .fromTo(el, { scaleX: 0, opacity: 1 }, { scaleX: 1, duration: 0.9, ease: "power1.inOut", transformOrigin: "0% 50%" })
              .to(el, { opacity: 0, duration: 0.3 }, ">-0.1");
          });
        }
        ScrollTrigger.create({
          trigger: scope.current,
          start: "top 75%",
          end: "bottom top",
          onEnter: () => intro.play(),
          onToggle: (self) => (self.isActive ? cycle?.play() : cycle?.pause()),
        });
      });
      return () => mm.revert();
    },
    { scope },
  );
}

/**
 * Sections below the fold rise in as they first come into view: one ScrollTrigger per
 * section (not per card), staggering that section's own items. Never replays.
 */
function useSectionReveal(scope: React.RefObject<HTMLElement | null>) {
  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        gsap.utils.toArray<HTMLElement>("[data-reveal]").forEach((section) => {
          const items = section.querySelectorAll("[data-reveal-item]");
          if (!items.length) return;
          gsap.from(items, {
            opacity: 0,
            y: 16,
            duration: 0.5,
            stagger: 0.07,
            scrollTrigger: { trigger: section, start: "top 80%", once: true },
          });
        });
      });
      return () => mm.revert();
    },
    { scope },
  );
}

export default function Landing() {
  const root = useRef<HTMLDivElement>(null);
  const hero = useRef<HTMLElement>(null);
  const loop = useRef<HTMLElement>(null);
  const roles = useRef<HTMLUListElement>(null);
  useHeroIntro(hero);
  useLoopMotion(loop);
  useSectionReveal(root);
  useSpotlight(roles, "[data-spot]");
  return (
    <div ref={root} className="min-h-dvh bg-canvas text-ink">
      <a href="#content" className="skip-link">
        Skip to content
      </a>
      <header className="sticky top-0 border-b border-line/70 bg-canvas/85 backdrop-blur-md" style={{ zIndex: "var(--z-sticky)" }}>
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
          <Brand />
          <nav aria-label="Site" className="flex items-center gap-1 sm:gap-2">
            <a href="#how" className="hidden rounded-lg px-3 py-2 text-sm font-medium text-ink-muted hover:text-ink md:block">
              How it works
            </a>
            <a href="#safeguards" className="hidden rounded-lg px-3 py-2 text-sm font-medium text-ink-muted hover:text-ink md:block">
              Safeguards
            </a>
            <a href="#roles" className="hidden rounded-lg px-3 py-2 text-sm font-medium text-ink-muted hover:text-ink md:block">
              Roles
            </a>
            <ThemeToggle />
            <Link to="/login" className="inline-flex min-h-10 items-center rounded-lg px-3 text-sm font-semibold text-ink hover:bg-subtle">
              Sign in
            </Link>
            <Link
              to="/signup"
              className="hidden min-h-10 items-center rounded-lg bg-primary px-4 text-sm font-semibold text-on-primary transition-colors hover:bg-primary-hover sm:inline-flex"
            >
              Patient sign-up
            </Link>
          </nav>
        </div>
      </header>

      <main id="content">
        {/* Hero */}
        <section ref={hero} className="relative isolate mx-auto grid max-w-6xl items-center gap-12 px-4 pb-16 pt-12 sm:px-6 sm:pt-16 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:gap-16 lg:pb-24 lg:pt-20">
          {/* The care network: drifts slowly, answers the pointer, stays behind the content. */}
          <div aria-hidden className="ambient-fade pointer-events-none absolute inset-0 -z-10">
            <CareNetwork />
          </div>
          <div data-hero="text" className="max-w-xl">
            <h1 className="text-[44px] font-semibold leading-[1.05] tracking-[-0.03em] text-ink sm:text-[56px] lg:text-[64px]">
              Next Best Action
            </h1>
            <p className="mt-5 text-xl leading-8 text-ink-muted sm:text-[22px]">
              The right message, to the right person, on the right channel, at the right moment.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <CtaLink to="/login" variant="accent" magnetic>
                Sign in <ArrowRight className="h-4 w-4" aria-hidden />
              </CtaLink>
              <CtaLink to="/signup" variant="quiet">
                Patients: create an account
              </CtaLink>
            </div>
            <p className="mt-4 text-[13px] leading-5 text-ink-subtle">
              Healthcare professionals and team members join through an invitation from their organization.
            </p>
          </div>
          <RecommendationPreview />
        </section>

        {/* Signal to outcome: the product in one line */}
        <section aria-labelledby="strip-title" className="mx-auto max-w-6xl px-4 pb-14 sm:px-6">
          <h2 id="strip-title" className="sr-only">
            From a patient signal to an outcome
          </h2>
          <DataToAction />
        </section>

        {/* Problem → approach, as a plain statement band */}
        <section data-reveal className="border-y border-line bg-surface">
          <div className="mx-auto grid max-w-6xl gap-8 px-4 py-12 sm:px-6 md:grid-cols-3 md:gap-0 md:divide-x md:divide-line">
            {[
              ["For HCPs", "Fewer, more relevant touches: the topic and channel each professional actually engages with."],
              ["For patients", "Outreach timed to the refill gap, on a channel the patient has agreed to."],
              ["For the organization", "One engine on one data foundation, on top of existing CRM and marketing tools."],
            ].map(([title, text]) => (
              <div key={title} data-reveal-item className="md:px-8 md:first:pl-0 md:last:pr-0">
                <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
                <p className="mt-1.5 text-[15px] leading-6 text-ink-muted">{text}</p>
              </div>
            ))}
          </div>
        </section>

        {/* The loop */}
        <section ref={loop} id="how" className="mx-auto max-w-6xl scroll-mt-20 px-4 py-16 sm:px-6 lg:py-24">
          <div className="max-w-2xl">
            <h2 className="text-[32px] font-semibold leading-tight tracking-[-0.02em] text-ink sm:text-4xl">
              From data to a decision a person can approve
            </h2>
            <p className="mt-3 text-[17px] leading-7 text-ink-muted">
              Each cycle runs the same eight steps for every patient and HCP, and keeps the options it turned down.
            </p>
          </div>
          <ol className="mt-12 grid gap-x-6 gap-y-8 sm:grid-cols-2 lg:grid-cols-4">
            {LOOP.map(([name, text], i) => (
              <li key={name} className="relative border-t-2 border-line pt-5">
                <span
                  aria-hidden
                  data-loop-sweep
                  className="absolute -top-[2px] left-0 h-[2px] w-full bg-primary-line opacity-0"
                />
                <span
                  aria-hidden
                  data-loop-mark
                  className={cx("absolute -top-[2px] left-0 h-[2px] w-12", name === "Gate" || name === "Review" ? "bg-accent" : "bg-primary")}
                />
                <div className="tabular text-[13px] font-semibold text-ink-subtle">{String(i + 1).padStart(2, "0")}</div>
                <h3 className="mt-1 text-[17px] font-semibold text-ink">{name}</h3>
                <p className="mt-1.5 text-sm leading-6 text-ink-muted">{text}</p>
              </li>
            ))}
          </ol>
        </section>

        {/* Explainable + compliant: asymmetric pair */}
        <section id="safeguards" data-reveal className="scroll-mt-20 bg-subtle/70">
          <div className="mx-auto grid max-w-6xl gap-5 px-4 py-16 sm:px-6 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] lg:py-24">
            <div data-reveal-item className="flex flex-col rounded-2xl border border-line bg-surface p-6 shadow-card sm:p-10">
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-primary-soft text-primary-ink" aria-hidden>
                <ListChecks className="h-5 w-5" />
              </span>
              <h2 className="mt-5 text-[28px] font-semibold leading-tight tracking-[-0.02em] text-ink">
                Every recommendation explains itself
              </h2>
              <p className="mb-8 mt-3 max-w-xl text-[15px] leading-7 text-ink-muted">
                Reasons are written from facts the engine already computed: risk drivers, response history, model
                estimates and gate results. Reviewers see why this person, why this action, why this channel and why now,
                plus any better option a safeguard held back.
              </p>
              <dl className="mt-auto grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-2">
                {[
                  ["Who", "Risk drivers with their points"],
                  ["What", "The action and the approved content"],
                  ["Where", "Channel response rates and consent"],
                  ["When", "Days until supply runs out"],
                ].map(([k, v]) => (
                  <div key={k} className="bg-surface px-4 py-3.5">
                    <dt className="text-[13px] font-semibold text-primary-ink">{k}</dt>
                    <dd className="mt-0.5 text-sm text-ink">{v}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <div className="flex flex-col gap-5">
              <div data-reveal-item className="flex-1 rounded-2xl bg-nav p-6 text-nav-ink sm:p-8">
                <span className="grid h-11 w-11 place-items-center rounded-xl bg-white/5 text-nav-indicator ring-1 ring-inset ring-white/10" aria-hidden>
                  <ShieldCheck className="h-5 w-5" />
                </span>
                <h2 className="mt-5 text-[22px] font-semibold leading-tight text-white">Safeguards are rules, not a model</h2>
                <ul className="mt-5 space-y-3 text-sm leading-6">
                  {[
                    "MLR-approved content inside its validity window",
                    "Patient consent for the channel, on the day",
                    "Contact frequency limits per person",
                  ].map((g) => (
                    <li key={g} className="flex gap-2.5">
                      <Check className="mt-1 h-4 w-4 shrink-0 text-nav-indicator" aria-hidden /> {g}
                    </li>
                  ))}
                </ul>
                <p className="mt-5 text-[13px] leading-5 text-nav-ink-muted">
                  Checked when a recommendation is created, again at approval and again at send.
                </p>
              </div>
              <div data-reveal-item className="rounded-2xl border border-line bg-surface p-6 shadow-card sm:p-8">
                <span className="grid h-11 w-11 place-items-center rounded-xl bg-accent-soft text-accent-ink" aria-hidden>
                  <MessageSquareText className="h-5 w-5" />
                </span>
                <h2 className="mt-5 text-lg font-semibold text-ink">The language model only drafts wording</h2>
                <p className="mt-2 text-sm leading-6 text-ink-muted">
                  It receives an action that already passed every safeguard, and its text is validated before anyone sees
                  it. A person approves before anything is sent.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* Measured, not claimed */}
        <section data-reveal className="mx-auto max-w-6xl px-4 py-16 sm:px-6 lg:py-24">
          <div className="grid gap-10 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-16">
            <div>
              <h2 className="text-[32px] font-semibold leading-tight tracking-[-0.02em] text-ink sm:text-4xl">
                Measured, not claimed
              </h2>
              <p className="mt-3 text-[17px] leading-7 text-ink-muted">
                Results are shown the way a reviewer would want to check them.
              </p>
            </div>
            <ul className="divide-y divide-line border-y border-line">
              {[
                [BarChart3, "Like-for-like comparison", "Engine outreach is compared with earlier outreach to the same kind of patient, already out of medication."],
                [Gauge, "Model quality against a baseline", "Every model is scored on held-out recent data next to a no-model baseline and a challenger."],
                [ScrollText, "Complete audit trail", "Each recommendation, safeguard result, decision, send and response is recorded with who acted."],
              ].map(([Icon, title, text]) => {
                const I = Icon as typeof BarChart3;
                return (
                  <li key={String(title)} data-reveal-item className="flex gap-4 py-5">
                    <I className="mt-0.5 h-5 w-5 shrink-0 text-primary-ink" aria-hidden />
                    <div>
                      <h3 className="text-[15px] font-semibold text-ink">{String(title)}</h3>
                      <p className="mt-1 text-sm leading-6 text-ink-muted">{String(text)}</p>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        </section>

        {/* Roles */}
        <section id="roles" data-reveal className="scroll-mt-20 border-t border-line bg-surface">
          <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6 lg:py-24">
            <div className="max-w-2xl">
              <h2 className="text-[32px] font-semibold leading-tight tracking-[-0.02em] text-ink sm:text-4xl">
                One platform, a workspace for each role
              </h2>
              <p className="mt-3 text-[17px] leading-7 text-ink-muted">
                Your account decides what you see. A care manager sees only assigned patients, a representative only
                assigned HCPs, a patient only their own record. Access is enforced on the server for every request.
              </p>
            </div>
            <ul ref={roles} className="mt-10 grid gap-x-10 sm:grid-cols-2 lg:grid-cols-3">
              {ROLES.map(({ icon: Icon, name, text }) => (
                <li key={name} data-reveal-item data-spot className="spotlight -mx-3 flex gap-3.5 rounded-xl border-t border-line px-3 py-5">
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-sage text-sage-ink" aria-hidden>
                    <Icon className="h-[18px] w-[18px]" />
                  </span>
                  <div>
                    <h3 className="text-[15px] font-semibold text-ink">{name}</h3>
                    <p className="mt-0.5 text-sm leading-6 text-ink-muted">{text}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* Closing CTA */}
        <section className="bg-nav">
          <div className="mx-auto flex max-w-6xl flex-col items-start justify-between gap-8 px-4 py-14 sm:px-6 md:flex-row md:items-center">
            <div className="max-w-xl">
              <h2 className="text-[28px] font-semibold leading-tight tracking-[-0.02em] text-white">
                See the workspace for your role
              </h2>
              <p className="mt-2 text-[15px] leading-6 text-nav-ink-muted">
                Professionals join by invitation and get the Verified mark; patients create their own account. Either
                way you confirm your email, and the workspace for your role opens with synthetic records.
              </p>
            </div>
            <div className="flex w-full flex-col gap-3 sm:w-auto sm:flex-row">
              <CtaLink to="/login" variant="accent" magnetic>
                Sign in <ArrowRight className="h-4 w-4" aria-hidden />
              </CtaLink>
              <CtaLink to="/signup" variant="light">
                Patient sign-up
              </CtaLink>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-10 sm:px-6 md:flex-row md:items-start md:justify-between">
          <div className="max-w-md">
            <Brand />
            <p className="mt-3 text-[13px] leading-5 text-ink-subtle">
              Proof of concept. All people and records are synthetic. A communication-decision tool, not a clinical one,
              and not medical advice.
            </p>
          </div>
          <nav aria-label="Footer" className="flex flex-wrap gap-x-8 gap-y-3 text-sm">
            <div className="space-y-2">
              <div className="font-semibold text-ink">Product</div>
              <a href="#how" className="block text-ink-muted hover:text-ink">How it works</a>
              <a href="#safeguards" className="block text-ink-muted hover:text-ink">Safeguards</a>
              <a href="#roles" className="block text-ink-muted hover:text-ink">Roles</a>
            </div>
            <div className="space-y-2">
              <div className="font-semibold text-ink">Account</div>
              <Link to="/login" className="flex items-center gap-1.5 text-ink-muted hover:text-ink">
                <UserCheck className="h-3.5 w-3.5" aria-hidden /> Sign in
              </Link>
              <Link to="/signup" className="flex items-center gap-1.5 text-ink-muted hover:text-ink">
                <ClipboardList className="h-3.5 w-3.5" aria-hidden /> Patient sign-up
              </Link>
            </div>
            <div className="space-y-2">
              <div className="font-semibold text-ink">Legal</div>
              <Link to="/legal/privacy" className="block text-ink-muted hover:text-ink">Privacy Policy</Link>
              <Link to="/legal/terms" className="block text-ink-muted hover:text-ink">Terms & Conditions</Link>
              <Link to="/legal/cookies" className="block text-ink-muted hover:text-ink">Cookie Policy</Link>
            </div>
          </nav>
        </div>
      </footer>
    </div>
  );
}
