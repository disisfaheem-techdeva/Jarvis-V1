"use client";

import { ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { ConsoleHeader, SeverityStripe, severityTone } from "@/components/FounderBits";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Notice } from "@/components/ui/Notice";
import { Skeleton } from "@/components/ui/Skeleton";
import { getIncidents, type Incident } from "@/lib/api";
import { humanize, relativeTime } from "@/lib/labels";

export default function IncidentsPage() {
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getIncidents().then((r) => (r.ok ? setIncidents(r.data) : setError(r.error)));
  }, []);

  const open = incidents?.filter((i) => !i.resolved) ?? [];
  const sorted = incidents
    ? [...incidents].sort((a, b) => Number(a.resolved) - Number(b.resolved) || new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    : null;

  return (
    <div className="flex flex-col gap-8">
      <ConsoleHeader
        title="Incidents"
        description="What broke, how bad it was, and whether it's fixed. Open incidents first."
        live={error ? false : incidents ? true : null}
      />

      {error ? <Notice tone="offline" title="Could not load">{error}</Notice> : null}

      {!sorted && !error ? (
        <div className="space-y-3">
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
        </div>
      ) : null}

      {sorted && sorted.length === 0 ? (
        <EmptyState icon={ShieldCheck} title="No incidents recorded" body="When something breaks, it's logged here with its severity and when it was resolved." />
      ) : null}

      {sorted && sorted.length > 0 ? (
        <section aria-labelledby="inc-title">
          <h2 id="inc-title" className="text-sm text-fg-muted">
            <span className="font-semibold text-fg">{open.length} open</span> · {sorted.length - open.length} resolved
          </h2>
          <ul className="mt-3 flex flex-col gap-2">
            {sorted.map((i) => {
              const tone = severityTone(i.severity);
              return (
                <li key={i.id} className="relative overflow-hidden rounded-[var(--p-radius-md)] border border-line bg-surface">
                  <SeverityStripe tone={i.resolved ? "neutral" : tone} />
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 py-4 pl-6 pr-5">
                    <p className="min-w-0 flex-1 font-medium text-fg">{humanize(i.type)}</p>
                    <Badge tone={tone}>{humanize(i.severity)}</Badge>
                    <Badge tone={i.resolved ? "success" : "danger"} dot>
                      {i.resolved ? "Resolved" : "Open"}
                    </Badge>
                    <time dateTime={i.created_at} title={new Date(i.created_at).toLocaleString()} className="w-24 text-right text-sm text-fg-subtle">
                      {relativeTime(i.created_at)}
                    </time>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
