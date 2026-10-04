import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useEffect } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, query } from "../api";
import { useAuth } from "../auth";
import { LEGAL_LINKS, LegalDocumentView, LegalLinks, legalPath } from "../legal";
import type { LegalDocumentData } from "../legal";
import { ThemeToggle } from "../theme";
import { ErrorState, Skeleton, cx } from "../ui";
import { Brand } from "./AuthLayout";

/** /legal/:kind: readable signed in or out, at any screen size and zoom. */
export default function LegalPage() {
  const { kind = "privacy" } = useParams();
  const [params] = useSearchParams();
  const version = params.get("version") ?? undefined;
  const { user } = useAuth();
  const doc = useQuery({
    queryKey: ["legal", kind, version],
    queryFn: () => api<LegalDocumentData>(`/legal/documents/${kind}${query({ version })}`),
    retry: false,
  });
  useEffect(() => {
    if (doc.data) document.title = `${doc.data.title} · Next Best Action`;
    return () => {
      document.title = "Next Best Action · Healthcare engagement";
    };
  }, [doc.data]);

  return (
    <div className="min-h-dvh bg-canvas">
      <header className="border-b border-line bg-canvas/90">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
          <Brand />
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Link
              to={user ? "/privacy" : "/"}
              className="inline-flex min-h-10 items-center gap-1.5 rounded-lg px-3 text-sm font-semibold text-ink hover:bg-subtle"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden />
              <span className="hidden sm:inline">{user ? "Data & privacy" : "Home"}</span>
            </Link>
          </div>
        </div>
        <nav aria-label="Legal documents" className="mx-auto flex max-w-6xl gap-1 overflow-x-auto px-4 pb-2 [scrollbar-width:none] sm:px-6 [&::-webkit-scrollbar]:hidden">
          {LEGAL_LINKS.map((l) => (
            <Link
              key={l.kind}
              to={legalPath(l.kind)}
              aria-current={l.kind === kind ? "page" : undefined}
              className={cx(
                "whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium",
                l.kind === kind ? "bg-primary-soft text-primary-ink" : "text-ink-muted hover:bg-subtle hover:text-ink",
              )}
            >
              {l.label}
            </Link>
          ))}
        </nav>
      </header>
      <main id="main" className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-12">
        {doc.isLoading ? (
          <div className="max-w-3xl space-y-4" role="status" aria-label="Loading document">
            <Skeleton className="h-10 w-2/3" />
            <Skeleton className="h-24" />
            <Skeleton className="h-64" />
          </div>
        ) : doc.error ? (
          <ErrorState error={doc.error} retry={() => void doc.refetch()} variant="page" title="This document could not be loaded" />
        ) : (
          doc.data && <LegalDocumentView doc={doc.data} />
        )}
      </main>
      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-6 text-[13px] text-ink-subtle sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <span>Designed with applicable privacy and security requirements in mind. Synthetic demonstration data.</span>
          <LegalLinks />
        </div>
      </footer>
    </div>
  );
}
