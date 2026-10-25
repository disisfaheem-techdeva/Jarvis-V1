"use client";

import { useEffect, useRef, useState } from "react";

import { AuthShell, PasswordInput } from "@/components/AuthShell";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { Notice } from "@/components/ui/Notice";
import { API_BASE } from "@/lib/api";
import { SIGNUP_URL } from "@/lib/links";

type Creds = { email: string; password: string };

async function postJson(path: string, body: unknown) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  return { res, data };
}

export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // an account whose email is not confirmed yet signs in only after the 6-digit code
  const [verify, setVerify] = useState<Creds | null>(null);
  const [status, setStatus] = useState<string>("");
  const [wait, setWait] = useState(0);
  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (wait <= 0) return;
    const t = setTimeout(() => setWait((w) => w - 1), 1000);
    return () => clearTimeout(t);
  }, [wait]);

  useEffect(() => {
    if (verify) codeRef.current?.focus();
  }, [verify]);

  function openDashboard(who: { role?: string } | null) {
    // the founder works in the founder console, a business in its dashboard
    window.location.href =
      who && who.role === "techdeva_admin" ? "https://api.techdeva.net/techdeva" : "https://api.techdeva.net/dashboard";
  }

  async function signIn(creds: Creds) {
    const { res, data } = await postJson("/auth/login", creds);
    if (res.status === 403 && data && data.verification_required) {
      setVerify(creds);
      setStatus(data.email_sent ? `We sent a 6-digit code to ${creds.email}. Check the spam folder too.` : "");
      setWait(Math.min(data.retry_after || 60, 60));
      if (!data.email_sent && !data.retry_after) setError('We could not send your code just now. Use "Resend code" in a minute.');
      setLoading(false);
      return;
    }
    if (!res.ok) {
      setError("Could not sign in — check your details and try again.");
      setLoading(false);
      return;
    }
    openDashboard(data);
  }

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    try {
      await signIn({ email: String(form.get("email") || ""), password: String(form.get("password") || "") });
    } catch {
      setError("Could not reach the server.");
      setLoading(false);
    }
  }

  async function onVerify(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!verify) return;
    const code = String(new FormData(e.currentTarget).get("code") || "").replace(/\D/g, "");
    setError(null);
    if (code.length !== 6) {
      setError("Enter all 6 digits of the code.");
      return;
    }
    setLoading(true);
    try {
      const { res } = await postJson("/auth/verify-email", { email: verify.email, code });
      if (!res.ok) {
        setError(
          res.status === 429
            ? "Too many attempts. Wait a few minutes and try again."
            : 'That code is wrong or has expired. Check it, or use "Resend code".',
        );
        setLoading(false);
        return;
      }
      setStatus("Email confirmed. Signing you in…");
      await signIn(verify);
    } catch {
      setError("Could not reach the server.");
      setLoading(false);
    }
  }

  async function onResend() {
    if (!verify || wait > 0) return;
    setError(null);
    try {
      const { res, data } = await postJson("/auth/resend-code", { email: verify.email });
      if (!res.ok) {
        setError("Too many requests. Try again later.");
        return;
      }
      setStatus(`A new code is on its way to ${verify.email}. Only the newest code works.`);
      setWait((data && data.retry_after) || 60);
    } catch {
      setError("Could not reach the server.");
    }
  }

  if (verify) {
    return (
      <AuthShell>
        <h1 className="font-display text-2xl font-semibold tracking-tight text-fg">Check your email</h1>
        <p className="mt-2 text-[15px] text-fg-muted">
          Enter the 6-digit code sent to <strong className="text-fg">{verify.email}</strong>. It expires in 10 minutes.
        </p>

        <form onSubmit={onVerify} className="mt-7 flex flex-col gap-4" noValidate>
          <Field label="Verification code" required>
            {(p) => (
              <Input
                {...p}
                ref={codeRef}
                name="code"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                maxLength={6}
                placeholder="123456"
              />
            )}
          </Field>

          <p className="text-sm text-fg-muted" aria-live="polite">
            {status}
          </p>

          {error ? (
            <Notice tone="danger" title="Not confirmed" live>
              {error}
            </Notice>
          ) : null}

          <Button type="submit" size="lg" disabled={loading} className="w-full">
            {loading ? "Checking…" : "Confirm and log in"}
          </Button>
          <Button type="button" variant="secondary" size="lg" disabled={wait > 0} onClick={onResend} className="w-full">
            {wait > 0 ? `Resend code (${wait}s)` : "Resend code"}
          </Button>
        </form>

        <div className="mt-6 text-sm text-fg-muted">
          <button
            type="button"
            className="inline-flex min-h-9 items-center font-medium text-accent-ink hover:underline"
            onClick={() => {
              setVerify(null);
              setError(null);
              setStatus("");
            }}
          >
            Use a different email
          </button>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell>
      <h1 className="font-display text-2xl font-semibold tracking-tight text-fg">Log in</h1>
      <p className="mt-2 text-[15px] text-fg-muted">Open your TechDeva dashboard.</p>

      <form onSubmit={onSubmit} className="mt-7 flex flex-col gap-4">
        <Field label="Email" required>
          {(p) => <Input {...p} name="email" type="email" autoComplete="email" placeholder="you@yourbusiness.com" />}
        </Field>
        <Field label="Password" required>
          {(p) => <PasswordInput {...p} name="password" autoComplete="current-password" />}
        </Field>

        {error ? (
          <Notice tone="danger" title="Not signed in" live>
            {error}
          </Notice>
        ) : null}

        <Button type="submit" size="lg" disabled={loading} className="mt-2 w-full">
          {loading ? "Signing in…" : "Log in"}
        </Button>
      </form>

      <div className="mt-6 flex flex-col gap-2 text-sm text-fg-muted">
        <a className="inline-flex min-h-9 items-center font-medium text-accent-ink hover:underline" href={`${API_BASE}/reset`}>
          Forgot your password?
        </a>
        <p>
          New to TechDeva?{" "}
          <a className="font-medium text-accent-ink hover:underline" href={SIGNUP_URL}>
            Start a free trial
          </a>
        </p>
      </div>
    </AuthShell>
  );
}
