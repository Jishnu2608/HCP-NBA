// Short-lived confirmations for completed actions ("Recommendation approved"). Errors that
// need reading stay inline next to the control that caused them; toasts are for success.
import { CheckCircle2, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { cx } from "./ui";

interface Toast {
  id: number;
  message: ReactNode;
  tone: "ok" | "info";
}

const ToastContext = createContext<(message: ReactNode, tone?: Toast["tone"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);
  const show = useCallback(
    (message: ReactNode, tone: Toast["tone"] = "ok") => {
      const id = next.current++;
      setToasts((all) => [...all.slice(-2), { id, message, tone }]);
      window.setTimeout(() => dismiss(id), 4200);
    },
    [dismiss],
  );
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
          <div
            key={t.id}
            role="status"
            className={cx(
              "animate-rise pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl border px-4 py-3 shadow-overlay",
              "border-line bg-surface text-sm text-ink",
            )}
          >
            {t.tone === "ok" ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-ok" aria-hidden />
            ) : (
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-info" aria-hidden />
            )}
            <div className="min-w-0 flex-1 font-medium">{t.message}</div>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => dismiss(t.id)}
              className="-m-1 rounded-md p-1 text-ink-subtle hover:text-ink"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
