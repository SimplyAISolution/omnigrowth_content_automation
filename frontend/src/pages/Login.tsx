import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/auth";
import { Field } from "../components/ui";
import { explainError } from "../lib/format";

export function LoginPage() {
  const { me, signIn, expired } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  if (me) return <Navigate to={from} replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setBusy(true);
    try {
      await signIn(email.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Email or password is incorrect." : explainError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login" id="main">
      <form className="card login-card" onSubmit={onSubmit} noValidate aria-describedby="login-intro">
        <h1>OmniGrowth</h1>
        <p id="login-intro" className="muted">
          Plan, review and schedule content for your brands. Content waits for a person's approval unless a brand turns on automatic
          approval, and publishing in this version is simulated.
        </p>
        {expired ? (
          <div className="notice notice-warning" role="status">
            Your session ended. Sign in again to continue.
          </div>
        ) : null}
        <Field label="Email" htmlFor="email">
          <input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </Field>
        <Field label="Password" htmlFor="password">
          <input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </Field>
        {error ? (
          <div className="notice notice-danger" role="alert">
            {error}
          </div>
        ) : null}
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="small muted">Accounts are created by an owner; there is no self sign-up.</p>
      </form>
    </main>
  );
}
