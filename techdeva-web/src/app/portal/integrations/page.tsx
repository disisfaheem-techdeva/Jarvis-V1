"use client";

import { AlertTriangle, CircleCheck, CircleDashed, Clock, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { SessionGate } from "@/components/SessionGate";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonClass } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Notice } from "@/components/ui/Notice";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { API_BASE } from "@/lib/api";
import { relativeTime } from "@/lib/labels";

/**
 * What this business can actually prove about each integration.
 *
 * The API computes `connected` from evidence — a stored credential and a real
 * connection moment — rather than from a status column, after an audit found
 * four rows claiming connected while holding no credential. This page shows
 * that evidence instead of collapsing everything to a green tick: a token that
 * has expired, a connection that errored, and one that was never made are
 * three different situations and the client is told which one they are in.
 */

interface Integration {
  provider: string;
  status: string;
  connected: boolean;
  health: string;
  external_account: string | null;
  connected_at: string | null;
  expires_at: string | null;
  last_checked_at: string | null;
  scopes: string[];
  last_error: string | null;
}

const LABEL: Record<string, string> = {
  meta: "Facebook & Instagram ads",
  instagram: "Instagram",
  google: "Google",
  whatsapp: "WhatsApp",
};

const BLURB: Record<string, string> = {
  meta: "Lets your AI team read ad performance and build campaigns for approval.",
  instagram: "Comes with Facebook — the same connection covers both.",
  google: "Search ads and your Business Profile.",
  whatsapp: "The number your AI sales agent answers on.",
};

/** Providers that cannot be self-connected yet — say so rather than show a dead button. */
const NOT_SELF_SERVICE = new Set(["whatsapp", "instagram"]);

/** Plain-language explanations for provider error codes. Unknown codes are shown as-is. */
const ERROR_TEXT: Record<string, string> = {
  invalid_grant: "The provider stopped accepting our access, usually after a password change or a removed permission.",
  expired_token: "The access expired.",
  access_denied: "Permission was declined on the provider's screen.",
};

function expired(i: Integration): boolean {
  return !!i.expires_at && new Date(i.expires_at).getTime() < Date.now();
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function State({ i }: { i: Integration }) {
  if (i.last_error) {
    return (
      <div className="flex items-start gap-2 text-sm">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-hidden="true" />
        <p>
          <span className="font-medium text-danger">Problem.</span>{" "}
          <span className="text-fg-muted">{ERROR_TEXT[i.last_error] ?? i.last_error} Reconnect to fix it.</span>
        </p>
      </div>
    );
  }
  if (i.connected && expired(i)) {
    return (
      <p className="flex items-center gap-2 text-sm">
        <Clock className="h-4 w-4 text-warning" aria-hidden="true" />
        <span className="font-medium text-warning">Expired</span>
        <span className="text-fg-muted">· needs reconnecting</span>
      </p>
    );
  }
  if (i.connected) {
    return (
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <CircleCheck className="h-4 w-4 text-success" aria-hidden="true" />
        <span className="font-medium text-success">Connected</span>
        {i.external_account ? <span className="text-fg-muted">· {i.external_account}</span> : null}
      </p>
    );
  }
  return (
    <p className="flex items-center gap-2 text-sm text-fg-muted">
      <CircleDashed className="h-4 w-4" aria-hidden="true" />
      Not connected
    </p>
  );
}

export default function IntegrationsPage() {
  return (
    <SessionGate title="Connections" purpose="manage your connections">
      {() => <Connections />}
    </SessionGate>
  );
}

function Connections() {
  const [items, setItems] = useState<Integration[] | null | undefined>(undefined);

  const load = useCallback(() => {
    fetch(`${API_BASE}/integrations`, { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setItems(d?.integrations ?? null))
      .catch(() => setItems(null));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const header = (
    <PageHeader
      title="Connections"
      description="Your AI team can only work with what is connected here. A connection shows as live only when a working credential is actually stored."
    />
  );

  if (items === undefined) {
    return (
      <div className="flex max-w-3xl flex-col gap-4">
        {header}
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-32 rounded-[var(--card-radius)]" />
        ))}
      </div>
    );
  }

  if (items === null) {
    return (
      <div className="max-w-3xl">
        {header}
        <Notice
          tone="offline"
          title="Couldn't load your connections"
          className="mt-6"
          action={
            <Button variant="secondary" size="sm" onClick={load}>
              Try again
            </Button>
          }
        >
          Nothing was changed.
        </Notice>
      </div>
    );
  }

  const live = items.filter((i) => i.connected && !expired(i) && !i.last_error).length;

  return (
    <div className="max-w-3xl">
      {header}
      <p className="mt-4 text-sm text-fg-subtle">
        {live} of {items.length} connected
      </p>

      <ul className="mt-4 flex flex-col gap-3">
        {items.map((i) => {
          const needsAction = !i.connected || expired(i) || !!i.last_error;
          const selfService = !NOT_SELF_SERVICE.has(i.provider);
          const evidence: string[] = [];
          if (i.connected && i.last_checked_at) evidence.push(`Last checked ${relativeTime(i.last_checked_at)}`);
          if (i.connected && i.expires_at && !expired(i)) evidence.push(`renews by ${fmtDate(i.expires_at)}`);
          return (
            <li key={i.provider}>
              <Card as="article" aria-labelledby={`c-${i.provider}`} className="flex flex-col gap-4 p-5 sm:flex-row sm:items-start sm:justify-between sm:p-6">
                <div className="min-w-0">
                  <h2 id={`c-${i.provider}`} className="text-base font-semibold text-fg">
                    {LABEL[i.provider] ?? i.provider}
                  </h2>
                  <p className="mt-1 text-sm text-fg-muted">{BLURB[i.provider] ?? ""}</p>
                  <div className="mt-3">
                    <State i={i} />
                  </div>
                  {evidence.length > 0 ? (
                    <p className="mt-2 flex items-center gap-1.5 text-xs text-fg-subtle">
                      <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                      {evidence.join(" · ")}
                    </p>
                  ) : null}
                  {i.scopes.length > 0 ? (
                    <p className="mt-1 text-xs text-fg-subtle">Permissions: {i.scopes.join(", ")}</p>
                  ) : null}
                </div>

                {needsAction && selfService ? (
                  <a href={`${API_BASE}/connect/${i.provider}`} className={buttonClass(i.connected || i.last_error ? "secondary" : "primary", "md", "shrink-0")}>
                    {i.connected || i.last_error ? "Reconnect" : "Connect"}
                  </a>
                ) : null}
                {needsAction && !selfService ? (
                  <Badge className="shrink-0 self-start">{i.provider === "instagram" ? "Connects through Facebook" : "We set this up for you"}</Badge>
                ) : null}
              </Card>
            </li>
          );
        })}
      </ul>

      <p className="mt-8 text-sm text-fg-subtle">
        Connecting opens the provider&apos;s own permission screen. TechDeva never sees or stores your password.
      </p>
    </div>
  );
}
