import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { isChallenge, useAuth } from "../auth";
import { mayOpen } from "../routes";
import { Button, ErrorNote } from "../ui";
import AuthLayout, { TextField } from "./AuthLayout";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

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
      title="Log in"
      subtitle="Your account decides which dashboard you see. There is nothing to choose after signing in."
    >
      <form onSubmit={submit} className="space-y-4">
        <TextField
          label="Email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoFocus
        />
        <TextField
          label="Password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <ErrorNote error={error} />
        <Button type="submit" variant="primary" busy={busy} className="w-full">
          Sign in
        </Button>
      </form>
      <p className="mt-5 text-center text-sm text-stone-600">
        New here?{" "}
        <Link to="/signup" className="font-medium text-brand-700 hover:underline">
          Create an account
        </Link>
      </p>
    </AuthLayout>
  );
}
