"use client";

import { CircleCheck, LinkIcon } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { AuthShell, PasswordInput } from "@/components/AuthShell";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Field } from "@/components/ui/Input";
import { Notice } from "@/components/ui/Notice";
import { Skeleton } from "@/components/ui/Skeleton";
import { API_BASE } from "@/lib/api";

/** Choose a first password from a one-time setup link. */
function Form() {
  const token = useSearchParams().get("token") ?? "";
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit() {
    setError(null);
    if (pw.length < 10) return setError("Use at least 10 characters.");
    if (pw !== again) return setError("The two passwords do not match.");
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/auth/set-password`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password: pw }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) setError(data?.detail ?? "Could not set your password.");
      else setDone(true);
    } catch {
      setError("Network error — nothing was changed.");
    }
    setBusy(false);
  }

  if (!token) {
    return (
      <div>
        <LinkIcon className="h-6 w-6 text-fg-subtle" aria-hidden="true" />
        <h1 className="mt-4 font-display text-2xl font-semibold tracking-tight text-fg">This link is incomplete</h1>
        <p className="mt-2 text-[15px] text-fg-muted">
          The setup link is missing its token. Open the link from your message again, or ask your TechDeva contact for a new one.
        </p>
      </div>
    );
  }

  if (done) {
    return (
      <div role="status">
        <CircleCheck className="h-7 w-7 text-success" aria-hidden="true" />
        <h1 className="mt-4 font-display text-2xl font-semibold tracking-tight text-fg">Password set</h1>
        <p className="mt-2 text-[15px] text-fg-muted">You can sign in now. This link will not work again.</p>
        <ButtonLink href="/login" size="lg" className="mt-6 w-full">
          Log in
        </ButtonLink>
      </div>
    );
  }

  const tooShort = pw.length > 0 && pw.length < 10;

  return (
    <div>
      <h1 className="font-display text-2xl font-semibold tracking-tight text-fg">Choose your password</h1>
      <p className="mt-2 text-[15px] text-fg-muted">At least 10 characters. Only you ever see it.</p>
      <form
        className="mt-7 flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <Field label="New password" required hint={tooShort ? `${10 - pw.length} more characters needed` : "At least 10 characters"}>
          {(p) => <PasswordInput {...p} autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />}
        </Field>
        <Field label="Repeat password" required>
          {(p) => <PasswordInput {...p} autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />}
        </Field>
        {error ? (
          <Notice tone="danger" title="Password not set" live>
            {error}
          </Notice>
        ) : null}
        <Button type="submit" size="lg" disabled={busy} className="mt-2 w-full">
          {busy ? "Saving…" : "Set password"}
        </Button>
      </form>
    </div>
  );
}

export default function SetPasswordPage() {
  return (
    <AuthShell>
      <Suspense
        fallback={
          <div className="space-y-4" aria-busy="true">
            <Skeleton className="h-8 w-56" />
            <Skeleton className="h-11" />
            <Skeleton className="h-11" />
          </div>
        }
      >
        <Form />
      </Suspense>
    </AuthShell>
  );
}
