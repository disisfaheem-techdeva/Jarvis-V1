"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Fire, WarningCircle } from "@phosphor-icons/react";
import { Button } from "../ui";

const field =
  "h-12 w-full rounded-[var(--radius-field)] border border-coal-600 bg-coal-850 px-4 text-ash-50 focus:border-ember-500 focus:outline-none";

export function AdminLogin({ configured }: { configured: boolean }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!configured) {
    return (
      <div className="mx-auto mt-16 max-w-md rounded-[var(--radius-card)] bg-coal-900 p-8 text-ash-300 ring-1 ring-white/[0.06]">
        <h1 className="text-xl font-semibold text-ash-50">Admin is not set up yet</h1>
        <p className="mt-3 text-sm leading-relaxed">
          Add <code className="text-ash-100">ADMIN_EMAIL</code>, <code className="text-ash-100">ADMIN_PASSWORD</code> (12+
          characters) and <code className="text-ash-100">ADMIN_SESSION_SECRET</code> (32+ characters) to the server
          environment, then reload.
        </p>
      </div>
    );
  }

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const fd = new FormData(e.currentTarget);
    try {
      const res = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: fd.get("email"), password: fd.get("password") }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Could not sign in");
        setBusy(false);
        return;
      }
      router.refresh();
    } catch {
      setError("No connection. Try again.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="mx-auto mt-16 max-w-sm space-y-5 rounded-[var(--radius-card)] bg-coal-900 p-8 ring-1 ring-white/[0.06]">
      <div className="flex items-center gap-2">
        <span className="grid size-9 place-items-center rounded-full bg-ember-500 text-coal-950">
          <Fire size={18} weight="fill" />
        </span>
        <h1 className="text-xl font-semibold text-ash-50">Owner sign in</h1>
      </div>
      <div className="flex flex-col gap-2">
        <label htmlFor="email" className="text-sm text-ash-100">
          Email
        </label>
        <input id="email" name="email" type="email" autoComplete="username" required className={field} />
      </div>
      <div className="flex flex-col gap-2">
        <label htmlFor="password" className="text-sm text-ash-100">
          Password
        </label>
        <input id="password" name="password" type="password" autoComplete="current-password" required className={field} />
      </div>
      {error && (
        <p role="alert" className="flex items-center gap-2 text-sm text-danger">
          <WarningCircle size={16} /> {error}
        </p>
      )}
      <Button type="submit" size="lg" className="w-full" disabled={busy}>
        {busy ? "Signing in..." : "Sign in"}
      </Button>
    </form>
  );
}
