// Full-screen error pages and the crash boundary. The error panel itself (icon, code,
// title, explanation, recovery actions) is `ErrorPanel` in ui.tsx, shared by every error
// state in the product.
import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { ThemeToggle } from "../theme";
import { ErrorPanel } from "../ui";
import type { ErrorKind } from "../ui";
import { Brand } from "./AuthLayout";

/** An error page outside the signed-in shell: brand, theme switch, the panel, a footer. */
export function ErrorScreen({ kind, onRetry }: { kind: ErrorKind; onRetry?: () => void }) {
  return (
    <div className="flex min-h-dvh flex-col bg-canvas">
      <header className="border-b border-line">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
          <Brand />
          <ThemeToggle />
        </div>
      </header>
      <main id="main" className="flex flex-1 items-center px-4 sm:px-6">
        <ErrorPanel kind={kind} onRetry={onRetry} />
      </main>
      <footer className="px-4 py-6 text-center text-xs text-ink-subtle">
        Proof of concept with synthetic data. A communication-decision tool, not a clinical one.
      </footer>
    </div>
  );
}

/**
 * Catches anything a page throws while rendering, so a bug shows the product's error
 * page instead of a blank screen. Inside the shell (`standalone` false) the navigation and
 * theme switch stay usable; the boundary resets when the address changes. The error is
 * logged to the console for developers and never shown to the user.
 */
export class AppErrorBoundary extends Component<
  { children: ReactNode; standalone?: boolean; resetKey?: string },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("Unexpected application error", error, info.componentStack);
  }

  componentDidUpdate(previous: { resetKey?: string }) {
    if (this.state.failed && previous.resetKey !== this.props.resetKey) this.setState({ failed: false });
  }

  render() {
    if (!this.state.failed) return this.props.children;
    // A reload is the reliable recovery: it also fetches page files replaced by a deploy.
    const retry = () => window.location.reload();
    return this.props.standalone ? <ErrorScreen kind="server" onRetry={retry} /> : <ErrorPanel kind="server" onRetry={retry} />;
  }
}
