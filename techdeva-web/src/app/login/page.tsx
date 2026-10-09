"use client";

import { useState } from "react";

import { AuthShell, PasswordInput } from "@/components/AuthShell";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { Notice } from "@/components/ui/Notice";
import { API_BASE } from "@/lib/api";
import { SIGNUP_URL } from "@/lib/links";

export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          email: form.get("email"),
          password: form.get("password"),
        }),
      });
      if (!res.ok) {
        setError("Could not sign in — check your details and try again.");
        setLoading(false);
        return;
      }
      // the founder works in the founder console, a business in its dashboard
      const who = await res.json().catch(() => null);
      window.location.href =
        who && who.role === "techdeva_admin" ? "https://api.techdeva.net/techdeva" : "https://api.techdeva.net/dashboard";
    } catch {
      setError("Could not reach the server.");
      setLoading(false);
    }
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
