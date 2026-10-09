"use client";

import { useEffect, useState } from "react";

import { ConsoleHeader } from "@/components/FounderBits";
import { MetricCard } from "@/components/MetricCard";
import { Notice } from "@/components/ui/Notice";
import { getOrchestratorStatus, type OrchestratorStatus } from "@/lib/api";

export default function OrchestratorPage() {
  const [status, setStatus] = useState<OrchestratorStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    getOrchestratorStatus()
      .then((r) => (r.ok ? setStatus(r.data) : setError(r.error)))
      .finally(() => setDone(true));
  }, []);

  return (
    <div className="flex flex-col gap-8">
      <ConsoleHeader
        title="Orchestrator"
        description="The rulebook and task router. Events come in, tasks go out to agents, and anything irreversible parks for a human."
        live={!done ? null : !error}
      />
      {error ? <Notice tone="offline" title="Could not load">{error}</Notice> : null}
      <ul className="td-stagger grid grid-cols-2 gap-3 sm:gap-4">
        <MetricCard i={0} label="Events queue" value={status?.queue?.events ?? null} help="Events waiting to be routed" loading={!done} />
        <MetricCard i={1} label="Tasks queue" value={status?.queue?.tasks ?? null} help="Tasks waiting for an agent" loading={!done} />
      </ul>
    </div>
  );
}
