// The product's motion vocabulary, built on GSAP. Every animation in the app uses these few
// pieces, so motion reads as one language and stays inside one performance budget.
//
// Principles (approved motion plan, 2026-10-08):
// - Motion explains: hierarchy on entry, a change of state, where something went. Nothing
//   loops on app pages, and nothing animates because it can.
// - Only transform and opacity (plus SVG stroke for drawing): no layout properties.
// - No animation sets React state, so motion never causes a render.
// - Every hook runs inside useGSAP, scoped to its component, so tweens are reverted on
//   unmount automatically.
// - Reduced motion: durations collapse to nothing; state changes stay visible as text.
// - Touch screens get fewer staggered items and no pointer effects.
//
// Tokens: micro 0.18 s (hover, press, small state), standard 0.32 s (cards, panels,
// drawers), expressive 0.6 s (landing only).
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { useRef } from "react";
import type { ReactNode, RefObject } from "react";
import { preferences } from "./session";

gsap.registerPlugin(useGSAP);

export const DUR = { micro: 0.18, standard: 0.32, expressive: 0.6 } as const;
export const EASE = { out: "power2.out", in: "power2.in", inOut: "power2.inOut" } as const;
gsap.defaults({ duration: DUR.standard, ease: EASE.out });

export const MEDIA = {
  reduce: "(prefers-reduced-motion: reduce)",
  desktopFine: "(min-width: 1024px) and (pointer: fine)",
  touch: "(pointer: coarse)",
} as const;

const matches = (q: string) => typeof window !== "undefined" && (window.matchMedia?.(q).matches ?? false);
export const reducedMotion = () => matches(MEDIA.reduce);
export const isTouch = () => matches(MEDIA.touch);
export const desktopFine = () => matches(MEDIA.desktopFine);

export { gsap, useGSAP };

/* ------------------------------------------------------------------ entrance */

/**
 * Children fade and lift in once, in order, when the component mounts. Only the first
 * `max` children move (4 on touch screens); the rest are simply there. Never runs again on
 * a refetch or re-render.
 */
export function useEntrance(
  scope: RefObject<HTMLElement | null>,
  { selector, max = 8, y = 8, delay = 0 }: { selector?: string; max?: number; y?: number; delay?: number } = {},
) {
  useGSAP(
    () => {
      const root = scope.current;
      if (!root || reducedMotion()) return;
      const all = selector ? Array.from(root.querySelectorAll<HTMLElement>(selector)) : (Array.from(root.children) as HTMLElement[]);
      const items = all.slice(0, isTouch() ? Math.min(4, max) : max);
      if (!items.length) return;
      gsap.from(items, { opacity: 0, y, delay, duration: DUR.standard, stagger: 0.04, clearProps: "opacity,transform" });
    },
    { scope },
  );
}

/* ------------------------------------------------------------------ change */

/**
 * Animates its content when `value` changes (never on first render): the new state fades in
 * with a slight scale, so the change is noticed. For status badges and counts in place.
 */
export function Morph({ value, children, className }: { value: unknown; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const first = useRef(true);
  useGSAP(
    () => {
      if (first.current) {
        first.current = false;
        return;
      }
      if (!ref.current || reducedMotion()) return;
      gsap.fromTo(ref.current, { opacity: 0, scale: 0.9 }, { opacity: 1, scale: 1, duration: DUR.micro, clearProps: "opacity,transform" });
    },
    { dependencies: [String(value)] },
  );
  return (
    <span ref={ref} className={className ?? "inline-flex max-w-full"}>
      {children}
    </span>
  );
}

/**
 * Items inside `scope` carrying `data-motion-id` and `data-motion-sig` (a summary of their
 * state) lift in briefly when they are new or their state changed since the last render.
 * Nothing moves on first render, and unchanged items never move, so long lists stay still.
 */
export function useChangeHighlight(scope: RefObject<HTMLElement | null>, deps: unknown[]) {
  const seen = useRef<Map<string, string> | null>(null);
  useGSAP(
    () => {
      const root = scope.current;
      if (!root) return;
      const items = Array.from(root.querySelectorAll<HTMLElement>("[data-motion-id]"));
      const now = new Map(items.map((el) => [el.dataset.motionId!, el.dataset.motionSig ?? ""]));
      const before = seen.current;
      seen.current = now;
      if (!before || reducedMotion()) return;
      const changed = items.filter((el) => before.get(el.dataset.motionId!) !== (el.dataset.motionSig ?? "")).slice(0, 6);
      if (!changed.length) return;
      gsap.fromTo(changed, { opacity: 0.35, y: 6 }, { opacity: 1, y: 0, duration: DUR.standard, stagger: 0.05, clearProps: "opacity,transform" });
    },
    { scope, dependencies: deps },
  );
}

/* ------------------------------------------------------------------ exit */

/**
 * Plays an exit (fade and slide) on the given elements, then calls `done`. Used by drawers,
 * menus and toasts, which otherwise vanish the moment React unmounts them.
 */
export function playExit(targets: Array<Element | null | undefined>, done: () => void, { x = 0, y = 0 }: { x?: number; y?: number } = {}) {
  const els = targets.filter(Boolean) as Element[];
  if (!els.length || reducedMotion()) return done();
  gsap.to(els, { opacity: 0, x, y, duration: 0.2, ease: EASE.in, overwrite: true, onComplete: done });
}

/* ------------------------------------------------------------------ "seen" */

/**
 * Whether `value` differs from what this viewer saw last time under `key`, recording the
 * new value. First sight returns false: nothing animates on a first visit or every load.
 */
export function changedSinceSeen(key: string, value: string): boolean {
  const last = preferences.lastSeen(key);
  if (last !== value) preferences.setLastSeen(key, value);
  return last !== null && last !== value;
}

/* ------------------------------------------------------------------ pointer */

/**
 * A very small pull toward the pointer for one important call to action (landing page
 * only). Desktop with a mouse only; quickTo reuses one tween per axis.
 */
export function useMagnetic(ref: RefObject<HTMLElement | null>, strength = 0.18) {
  useGSAP(
    (_, contextSafe) => {
      const el = ref.current;
      if (!el || !desktopFine() || reducedMotion() || !contextSafe) return;
      const xTo = gsap.quickTo(el, "x", { duration: 0.4, ease: "power3" });
      const yTo = gsap.quickTo(el, "y", { duration: 0.4, ease: "power3" });
      const move = contextSafe((e: PointerEvent) => {
        const r = el.getBoundingClientRect();
        xTo((e.clientX - (r.left + r.width / 2)) * strength);
        yTo((e.clientY - (r.top + r.height / 2)) * strength);
      });
      const leave = contextSafe(() => {
        xTo(0);
        yTo(0);
      });
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerleave", leave);
      return () => {
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerleave", leave);
      };
    },
    { scope: ref },
  );
}

/**
 * A soft light that follows the pointer across the cards of a grid (`[data-spot]`), so the
 * dashboard answers the mouse without moving anything. One delegated listener per grid,
 * one quickSetter pair per card, created on first hover. Desktop mouse only; nothing under
 * reduced motion or on touch screens.
 */
export function useSpotlightGrid(scope: RefObject<HTMLElement | null>) {
  useGSAP(
    (_, contextSafe) => {
      const root = scope.current;
      if (!root || !desktopFine() || reducedMotion() || !contextSafe) return;
      const setters = new WeakMap<HTMLElement, [(v: number) => void, (v: number) => void]>();
      const move = contextSafe((e: PointerEvent) => {
        const card = (e.target as HTMLElement).closest<HTMLElement>("[data-spot]");
        if (!card || !root.contains(card)) return;
        let set = setters.get(card);
        if (!set) {
          set = [gsap.quickSetter(card, "--spot-x", "px") as (v: number) => void, gsap.quickSetter(card, "--spot-y", "px") as (v: number) => void];
          setters.set(card, set);
        }
        const r = card.getBoundingClientRect();
        set[0](e.clientX - r.left);
        set[1](e.clientY - r.top);
      });
      root.addEventListener("pointermove", move);
      return () => root.removeEventListener("pointermove", move);
    },
    { scope },
  );
}

/**
 * A soft light under the pointer on marketing cards (landing page only). Writes two CSS
 * variables with quickSetter; the gradient itself is CSS. Desktop with a mouse only.
 */
export function useSpotlight(scope: RefObject<HTMLElement | null>, selector: string) {
  useGSAP(
    (_, contextSafe) => {
      const root = scope.current;
      if (!root || !desktopFine() || reducedMotion() || !contextSafe) return;
      const cards = Array.from(root.querySelectorAll<HTMLElement>(selector));
      const offs = cards.map((card) => {
        const setX = gsap.quickSetter(card, "--spot-x", "px");
        const setY = gsap.quickSetter(card, "--spot-y", "px");
        const move = contextSafe((e: PointerEvent) => {
          const r = card.getBoundingClientRect();
          setX(e.clientX - r.left);
          setY(e.clientY - r.top);
        });
        card.addEventListener("pointermove", move);
        return () => card.removeEventListener("pointermove", move);
      });
      return () => offs.forEach((off) => off());
    },
    { scope },
  );
}
