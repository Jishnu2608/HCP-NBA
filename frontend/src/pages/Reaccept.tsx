import { useMutation, useQuery } from "@tanstack/react-query";
import { FileCheck2, LogOut, Trash2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, post } from "../api";
import { useAuth } from "../auth";
import { Checkbox, legalPath } from "../legal";
import { Alert, Button, ErrorNote, LoadingRows } from "../ui";
import AuthLayout from "./AuthLayout";

interface ConsentItem {
  kind: string;
  label: string;
  statement: string | null;
  document: string | null;
  current_version: string;
  state: string | null;
}

/**
 * Shown instead of the app while the account has something to accept: a new version of the
 * Terms or Privacy Policy, or (for patients) consent to process health information. The
 * server enforces this; the page only explains it and records the choice.
 */
export default function Reaccept() {
  const { user, refresh, logout } = useAuth();
  const navigate = useNavigate();
  const status = useQuery({
    queryKey: ["privacy", "status", "pending"],
    queryFn: () => api<{ consents: ConsentItem[]; pending: string[] }>("/privacy/status"),
  });
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const [submitted, setSubmitted] = useState(false);
  const pending = (status.data?.consents ?? []).filter((c) => status.data?.pending.includes(c.kind));
  const accept = useMutation({
    mutationFn: () => post("/privacy/accept", { kinds: pending.map((c) => c.kind) }),
    onSuccess: () => void refresh(),
  });
  const erase = useMutation({
    mutationFn: () =>
      post("/privacy/requests", {
        type: "erasure",
        details: "Requested instead of accepting updated terms or consent.",
      }),
  });
  const allTicked = pending.every((c) => ticked[c.kind]);
  const isNew = pending.some((c) => c.state === "accepted");

  return (
    <AuthLayout
      wide
      title={isNew ? "Updated terms to review" : "Before you continue"}
      subtitle={
        <>
          Signed in as <span className="font-semibold text-ink">{user?.email}</span>. To use the application, review and
          accept the items below. Nothing is ticked for you.
        </>
      }
    >
      {status.isLoading ? (
        <LoadingRows rows={3} label="Loading" />
      ) : status.error ? (
        <ErrorNote error={status.error} />
      ) : (
        <div className="space-y-6">
          <div className="space-y-4 rounded-xl border border-line bg-surface p-5 shadow-card">
            {pending.map((c) => (
              <Checkbox
                key={c.kind}
                checked={Boolean(ticked[c.kind])}
                onChange={(v) => setTicked({ ...ticked, [c.kind]: v })}
                error={submitted && !ticked[c.kind] ? "Required to continue." : null}
              >
                {c.document ? (
                  <>
                    {c.kind === "terms" ? "I have read and agree to the " : "I acknowledge the "}
                    <a
                      href={legalPath(c.document)}
                      target="_blank"
                      rel="noopener"
                      className="font-semibold text-primary-ink underline underline-offset-2"
                    >
                      {c.document === "terms" ? "Terms & Conditions" : "Privacy Policy"}
                    </a>{" "}
                    (version {c.current_version}).
                  </>
                ) : (
                  c.statement
                )}
              </Checkbox>
            ))}
            <p className="text-[13px] leading-5 text-ink-subtle">
              Your choice is recorded with the version and time. Withdrawing a consent later does not affect processing
              that happened before.
            </p>
          </div>
          <ErrorNote error={accept.error} />
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="primary"
              size="lg"
              busy={accept.isPending}
              onClick={() => {
                setSubmitted(true);
                if (allTicked) accept.mutate();
              }}
            >
              <FileCheck2 className="h-4 w-4" aria-hidden /> Accept and continue
            </Button>
            <Button variant="ghost" onClick={() => void logout().then(() => navigate("/", { replace: true }))}>
              <LogOut className="h-4 w-4" aria-hidden /> Sign out
            </Button>
          </div>
          <div className="border-t border-line pt-5">
            {erase.isSuccess ? (
              <Alert tone="info" title="Deletion request submitted">
                A person will review your request and contact you. Your account has not been deleted yet.
              </Alert>
            ) : (
              <div className="flex flex-wrap items-center gap-3 text-sm text-ink-muted">
                <span>Don't want to accept?</span>
                <a href="/privacy" className="font-semibold text-primary-ink underline underline-offset-2">
                  Open Data & privacy
                </a>
                <span aria-hidden>·</span>
                <Button size="sm" variant="quiet-danger" busy={erase.isPending} onClick={() => erase.mutate()}>
                  <Trash2 className="h-3.5 w-3.5" aria-hidden /> Request account deletion
                </Button>
              </div>
            )}
            <ErrorNote error={erase.error} className="mt-3" />
          </div>
        </div>
      )}
    </AuthLayout>
  );
}
