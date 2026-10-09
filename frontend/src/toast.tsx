// Short-lived confirmations for completed actions ("Recommendation approved"). Errors that
// need reading stay inline next to the control that caused them; toasts are for success.
// They slide in quickly and fade out before they are removed; they never block clicks
// around them (the stack itself lets pointer events through).
import { CheckCircle2, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { gsap, playExit, reducedMotion, useGSAP } from "./motion";
import { cx } from "./ui";

interface Toast {
  id: number;
  message: ReactNode;
  tone: "ok" | "info";
}

const ToastContext = createContext<(message: ReactNode, tone?: Toast["tone"]) => void>(() => {});
const LIFETIME_MS = 4200;

/**
 * One toast. It leaves on its own after LIFETIME_MS, but the clock pauses while the pointer
 * is over it or it has keyboard focus, so nobody loses a message they are reading (WCAG
 * 2.2.1). A thin bar shows the time left (transform only; hidden under reduced motion).
 */
function ToastItem({ toast, onGone }: { toast: Toast; onGone: (id: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLSpanElement>(null);
  const clock = useRef<gsap.core.Tween | null>(null);
  const leaving = useRef(false);
  const leave = useCallback(() => {
    if (leaving.current) return;
    leaving.current = true;
    clock.current?.kill();
    playExit([ref.current], () => onGone(toast.id), { y: 8 });
  }, [onGone, toast.id]);
  useGSAP(
    () => {
      const still = reducedMotion();
      if (ref.current && !still) gsap.from(ref.current, { y: 12, opacity: 0, duration: 0.2, clearProps: "transform,opacity" });
      clock.current = still
        ? gsap.delayedCall(LIFETIME_MS / 1000, leave)
        : gsap.fromTo(bar.current, { scaleX: 1 }, { scaleX: 0, duration: LIFETIME_MS / 1000, ease: "none", transformOrigin: "0% 50%", onComplete: leave });
    },
    { scope: ref },
  );
  const hold = () => clock.current?.pause();
  const release = () => {
    if (!ref.current?.matches(":hover, :focus-within")) clock.current?.resume();
  };
  return (
    <div
      ref={ref}
      role="status"
      onPointerEnter={hold}
      onPointerLeave={release}
      onFocus={hold}
      onBlur={release}
      className={cx(
        "pointer-events-auto relative flex w-full max-w-sm items-start gap-3 overflow-hidden rounded-xl border px-4 py-3 shadow-overlay",
        "border-line bg-surface text-sm text-ink",
      )}
    >
      {toast.tone === "ok" ? (
        <CheckCircle2 className="animate-pop mt-0.5 h-4 w-4 shrink-0 text-ok" aria-hidden />
      ) : (
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-info" aria-hidden />
      )}
      <div className="min-w-0 flex-1 font-medium">{toast.message}</div>
      <button type="button" aria-label="Dismiss" onClick={leave} className="-m-1 rounded-md p-1 text-ink-subtle hover:text-ink">
        <X className="h-4 w-4" aria-hidden />
      </button>
      <span
        ref={bar}
        aria-hidden
        className={cx("absolute inset-x-0 bottom-0 h-0.5 motion-reduce:hidden", toast.tone === "ok" ? "bg-ok-fill/60" : "bg-primary/50")}
      />
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);

  const remove = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);
  const show = useCallback((message: ReactNode, tone: Toast["tone"] = "ok") => {
    const id = next.current++;
    setToasts((all) => [...all.slice(-2), { id, message, tone }]);
  }, []);
  const value = useMemo(() => show, [show]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-0 flex flex-col items-center gap-2 p-4 sm:items-end sm:p-6"
        style={{ zIndex: "var(--z-toast)" }}
      >
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onGone={remove} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
