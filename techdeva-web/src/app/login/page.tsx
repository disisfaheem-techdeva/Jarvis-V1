"use client";

import { useState } from "react";
import Link from "next/link";
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
    <div className="flex min-h-screen items-center justify-center bg-[var(--background)] px-6">
      <div className="w-full max-w-sm td-card p-8">
        <Link href="/" className="flex items-center gap-2 text-base font-semibold text-white">
          <span className="h-2 w-2 rounded-full bg-[var(--accent)]" />
          TechDeva
        </Link>
        <h1 className="mt-5 text-xl font-semibold text-white">Log in</h1>
        <form onSubmit={onSubmit} className="mt-6 grid gap-3">
          <input
            name="email"
            type="email"
            required
            placeholder="Email"
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:outline-none"
          />
          <input
            name="password"
            type="password"
            required
            placeholder="Password"
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:outline-none"
          />
          {error ? <p className="text-sm text-[var(--danger)]">{error}</p> : null}
          <button
            type="submit"
            disabled={loading}
            className="td-btn-primary mt-2 px-4 py-2 text-sm disabled:opacity-50"
          >
            {loading ? "Signing in…" : "Log in"}
          </button>
        </form>
        <p className="mt-5 text-center text-sm text-[var(--muted)]">
          New to TechDeva?{" "}
          <a className="text-white underline" href={SIGNUP_URL}>Start a free trial</a>
        </p>
      </div>
    </div>
  );
}
