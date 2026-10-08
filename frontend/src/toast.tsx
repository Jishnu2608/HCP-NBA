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

function ToastItem({ toast, onGone }: { toast: Toast; onGone: (id: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const leaving = useRef(false);
  const leave = useCallback(() => {
    if (leaving.current) return;
    leaving.current = true;
    playExit([ref.current], () => onGone(toast.id), { y: 8 });
  }, [onGone, toast.id]);
  useGSAP(
    () => {
      if (ref.current && !reducedMotion()) gsap.from(ref.current, { y: 12, opacity: 0, duration: 0.2, clearProps: "transform,opacity" });
      const timer = window.setTimeout(leave, LIFETIME_MS);
      return () => window.clearTimeout(timer);
    },
    { scope: ref },
  );
  return (
    <div
      ref={ref}
      role="status"
      className={cx(
        "pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl border px-4 py-3 shadow-overlay",
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
