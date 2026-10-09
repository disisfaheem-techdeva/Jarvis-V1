"use client";

import { useEffect, useState } from "react";

import { ConsoleHeader } from "@/components/FounderBits";
import { ReadableValue } from "@/components/ReadableValue";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Notice } from "@/components/ui/Notice";
import { Skeleton } from "@/components/ui/Skeleton";
import { getAiHealth, type AiHealth } from "@/lib/api";
import { humanize } from "@/lib/labels";

export default function AiHealthPage() {
  const [health, setHealth] = useState<AiHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAiHealth().then((r) => (r.ok ? setHealth(r.data) : setError(r.error)));
  }, []);

  const status = health?.status?.toLowerCase();
  const healthy = status === "ok" || status === "healthy" || status === "up";
  const { provider, status: _s, ...rest } = health ?? {};
  void _s;

  return (
    <div className="flex flex-col gap-8">
      <ConsoleHeader title="AI health" description="Which model provider is serving the agents, and whether fallbacks are in use." live={error ? false : health ? true : null} />

      {error ? <Notice tone="offline" title="Could not load">{error}</Notice> : null}
      {!health && !error ? <Skeleton className="h-48 rounded-[var(--card-radius)]" /> : null}

      {health ? (
        <Card className="p-5 sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-5">
            <div>
              <p className="text-sm text-fg-subtle">Provider</p>
              <p className="mt-1 font-display text-2xl font-semibold text-fg">{provider ? humanize(String(provider)) : "—"}</p>
            </div>
            <Badge tone={healthy ? "success" : status ? "danger" : "neutral"} dot>
              {status ? humanize(status) : "Status unknown"}
            </Badge>
          </div>
          <div className="pt-5">
            {Object.keys(rest).length ? <ReadableValue value={rest} /> : <p className="text-sm text-fg-muted">No other details reported.</p>}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
