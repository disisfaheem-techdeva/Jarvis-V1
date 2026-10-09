"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { AuthShell, PasswordInput } from "@/components/AuthShell";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { Notice } from "@/components/ui/Notice";
import { API_BASE } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    try {
      const res = await fetch(`${API_BASE}/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
      router.push("/portal");
    } catch {
      setError("Could not reach the server.");
      setLoading(false);
    }
  }

  return (
    <AuthShell>
      <h1 className="font-display text-2xl font-semibold tracking-tight text-fg">Log in to your portal</h1>
      <p className="mt-2 text-[15px] text-fg-muted">See your leads, approvals and results.</p>

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

      <p className="mt-6 text-sm text-fg-subtle">
        Forgot your password or never set one? Ask your TechDeva contact for a new setup link.
      </p>
    </AuthShell>
  );
}
