// One application-wide theme. The colours themselves live only in index.css as design
// tokens; this module decides which token set is active by setting data-theme on <html>.
//
// Order of precedence: the user's explicit choice (saved locally) > the operating system.
// index.html applies the same rule before the app loads, so the first paint is correct.
import { Moon, Sun } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { preferences } from "./session";
import type { ThemeChoice } from "./session";
import { cx } from "./ui";

interface ThemeState {
  theme: ThemeChoice;
  /** True once the user has picked a theme; false while following the operating system. */
  explicit: boolean;
  setTheme: (theme: ThemeChoice) => void;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeState | null>(null);
const DARK_QUERY = "(prefers-color-scheme: dark)";

function systemTheme(): ThemeChoice {
  return window.matchMedia?.(DARK_QUERY).matches ? "dark" : "light";
}

function apply(theme: ThemeChoice) {
  const root = document.documentElement;
  if (root.dataset.theme === theme) return;
  // Switch instantly: no element should animate its colours while the theme changes.
  root.classList.add("theme-switching");
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  const canvas = getComputedStyle(root).getPropertyValue("--canvas").trim();
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", canvas);
  window.requestAnimationFrame(() => window.requestAnimationFrame(() => root.classList.remove("theme-switching")));
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [chosen, setChosen] = useState<ThemeChoice | null>(preferences.theme);
  const [system, setSystem] = useState<ThemeChoice>(systemTheme);
  const theme = chosen ?? system;

  useLayoutEffect(() => apply(theme), [theme]);

  // Follow the operating system live, until the user makes an explicit choice.
  useEffect(() => {
    const media = window.matchMedia?.(DARK_QUERY);
    if (!media) return;
    const onChange = () => setSystem(media.matches ? "dark" : "light");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const setTheme = useCallback((next: ThemeChoice) => {
    preferences.setTheme(next);
    setChosen(next);
  }, []);

  const value = useMemo<ThemeState>(
    () => ({ theme, explicit: chosen !== null, setTheme, toggle: () => setTheme(theme === "dark" ? "light" : "dark") }),
    [theme, chosen, setTheme],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme outside ThemeProvider");
  return ctx;
}

/**
 * The single theme switch. It appears in several headers but every instance controls the
 * same application-wide state. `onDark` styles it for the dark navigation surfaces.
 */
export function ThemeToggle({ onDark = false, className }: { onDark?: boolean; className?: string }) {
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  const label = `Switch to ${next} theme`;
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      title={label}
      className={cx(
        "inline-grid h-10 w-10 shrink-0 place-items-center rounded-lg transition-colors",
        onDark ? "text-nav-ink hover:bg-nav-raised hover:text-white" : "text-ink-muted hover:bg-subtle hover:text-ink",
        className,
      )}
    >
      {theme === "dark" ? <Sun className="h-5 w-5" aria-hidden /> : <Moon className="h-5 w-5" aria-hidden />}
    </button>
  );
}
