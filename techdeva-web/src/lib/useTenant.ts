"use client";

import { useCallback, useEffect, useState } from "react";

import { API_BASE } from "./api";

/**
 * The tenant the signed-in session belongs to.
 *
 * The browser does not get to choose this. Earlier builds read the tenant id
 * from `?tenant=<id>` or localStorage because no session endpoint had been
 * confirmed; the API now decides identity in `resolve_scope()` and ignores
 * client-supplied scope entirely, so the only honest source is `/me`, read
 * with the session cookie.
 *
 * Returns `undefined` while the session is still loading and `null` when
 * there is no session, so a caller can tell "not yet" from "not signed in".
 */
export function useTenant(): string | null | undefined {
  const { session } = useSession();
  if (session === undefined) return undefined;
  return session?.tenant_id ?? null;
}

export interface Session {
  tenant_id: string | null;
  role: string | null;
  user_id: string | null;
  authenticated: boolean;
  auth_enforced: boolean;
}

export type SessionStatus = "loading" | "signed-in" | "signed-out" | "unreachable";

/**
 * The signed-in session, read from `/me`.
 *
 * `session` keeps its original contract (undefined while loading, null when
 * there is no usable session). `status` adds the distinction the UI needs to
 * stay honest: a 401/unauthenticated answer is "signed-out", while a network
 * failure or server error is "unreachable" — the client may well be signed in,
 * and the screen must not claim otherwise.
 */
export function useSession() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/me`, { credentials: "include", cache: "no-store" })
      .then(async (res) => {
        if (cancelled) return;
        if (res.status >= 500) {
          setSession(null);
          setStatus("unreachable");
          return;
        }
        const data = (res.ok ? await res.json().catch(() => null) : null) as Session | null;
        if (cancelled) return;
        const ok = !!(data && data.authenticated);
        setSession(ok ? data : null);
        setStatus(ok ? "signed-in" : "signed-out");
      })
      .catch(() => {
        if (cancelled) return;
        setSession(null);
        setStatus("unreachable");
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = useCallback(() => {
    setSession(undefined);
    setStatus("loading");
    setAttempt((n) => n + 1);
  }, []);

  return { session, loading: session === undefined, status, retry };
}
