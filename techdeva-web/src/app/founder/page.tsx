"use client";

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { ConsoleHeader } from "@/components/FounderBits";
import { MetricCard } from "@/components/MetricCard";
import { Notice } from "@/components/ui/Notice";
import { getOrchestratorStatus, getTechDevaOverview, type OrchestratorStatus, type TechDevaOverview } from "@/lib/api";
import { clockTime } from "@/lib/labels";

export default function FounderOverview() {
  const [overview, setOverview] = useState<TechDevaOverview | null>(null);
  const [orch, setOrch] = useState<OrchestratorStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);

  useEffect(() => {
    Promise.all([
      getTechDevaOverview().then((r) => (r.ok ? setOverview(r.data) : setError(r.error))),
      getOrchestratorStatus().then((r) => (r.ok ? setOrch(r.data) : setError(r.error))),
    ]).finally(() => setLoadedAt(new Date()));
  }, []);

  const loading = !loadedAt;

  return (
    <div className="flex flex-col gap-8">
      <ConsoleHeader
        title="Overview"
        description="Every tenant, agent and queue across TechDeva, on one screen."
        live={loading ? null : !error}
      />

      {error ? (
        <Notice tone="offline" title={`Could not load live data (${error})`}>
          This console needs a real founder-scoped token wired to /techdeva/overview and /orchestrator/status.
        </Notice>
      ) : null}

      <ul className="td-stagger grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <MetricCard i={0} label="Tenants" value={overview?.tenants ?? null} help="Businesses on the platform" loading={loading} />
        <MetricCard i={1} label="Active agents" value={overview?.active_agents ?? null} help="AI employees currently running" loading={loading} />
        <MetricCard i={2} label="Revenue (MTD)" value={overview?.revenue_mtd ?? null} help="Month to date" empty="No revenue recorded this month" loading={loading} />
        <MetricCard i={3} label="Queued tasks" value={orch?.queue?.tasks ?? null} help="Waiting for an agent" loading={loading} />
      </ul>

      <div className="grid gap-4 md:grid-cols-3">
        {[
          { href: "/founder/orchestrator", title: "Orchestrator", body: "Event and task queues" },
          { href: "/founder/incidents", title: "Incidents", body: "What broke, and whether it's fixed" },
          { href: "/founder/ai-health", title: "AI health", body: "Model provider and fallback status" },
        ].map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="group flex items-center justify-between rounded-[var(--card-radius)] border border-line bg-surface p-5 transition-colors hover:border-accent-line"
          >
            <span>
              <span className="block font-semibold text-fg">{l.title}</span>
              <span className="mt-1 block text-sm text-fg-muted">{l.body}</span>
            </span>
            <ArrowUpRight className="h-5 w-5 text-fg-subtle transition-colors group-hover:text-accent-ink" aria-hidden="true" />
          </Link>
        ))}
      </div>

      {loadedAt ? <p className="text-xs text-fg-subtle">Loaded {clockTime(loadedAt)}.</p> : null}
    </div>
  );
}
