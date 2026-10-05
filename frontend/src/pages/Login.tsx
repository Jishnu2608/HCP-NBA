import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { isChallenge, useAuth } from "../auth";
import { mayOpen } from "../routes";
import { Alert, Button, ErrorNote, PasswordField, TextField } from "../ui";
import AuthLayout from "./AuthLayout";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  // Set when a signed-out visitor opened an app page (or a session ended): a 401.
  const signinRequired = (location.state as { reason?: string } | null)?.reason === "signin_required";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await login(email, password);
      if (isChallenge(result)) {
        navigate("/signup/verify");
        return;
      }
      // Return to the page that was asked for only if this account may open it;
      // otherwise go to the role's own dashboard.
      const wanted = (location.state as { from?: string } | null)?.from;
      navigate(mayOpen(wanted, result.permissions) ? wanted! : result.home, { replace: true });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Sign in"
      subtitle="Your account decides which workspace opens. There is nothing to choose after signing in."
      footer={
        <>
          New to Next Best Action?{" "}
          <Link to="/signup" className="font-semibold text-primary-ink underline-offset-4 hover:underline">
            Create a patient account
          </Link>
        </>
      }
    >
      {signinRequired && (
        <Alert tone="info" title="Please sign in to continue" className="mb-6">
          <span className="text-info">
            That page needs a signed-in account. If you were signed in, your session has ended.
          </span>
        </Alert>
      )}
      <form onSubmit={submit} className="space-y-5">
        <TextField
          label="Email"
          type="email"
          autoComplete="email"
          inputMode="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoFocus
        />
        <PasswordField
          label="Password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <ErrorNote error={error} />
        <Button type="submit" variant="primary" size="lg" busy={busy} className="w-full">
          {busy ? "Signing in" : "Sign in"}
        </Button>
      </form>
    </AuthLayout>
  );
}
