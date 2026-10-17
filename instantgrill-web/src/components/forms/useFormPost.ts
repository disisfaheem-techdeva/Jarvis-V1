"use client";

import { useState } from "react";

/** Posts a form as JSON; tracks busy, field errors, a form error and success. */
export function useFormPost(url: string) {
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(data: Record<string, unknown>): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(data) });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setErrors(body.fields ?? {});
        setError(body.error ?? "Something went wrong. Please try again.");
        return false;
      }
      setErrors({});
      setDone(true);
      return true;
    } catch {
      setError("No connection. Check your internet and try again.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  return { busy, errors, error, done, submit };
}
