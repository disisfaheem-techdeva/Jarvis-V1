"use client";

import { useEffect, useState } from "react";

import { MetricCard } from "@/components/MetricCard";
import { SessionGate } from "@/components/SessionGate";
import { Card } from "@/components/ui/Card";
import { Notice } from "@/components/ui/Notice";
import { PageHeader } from "@/components/ui/PageHeader";
import { getAttributionReport, type AttributionReport } from "@/lib/api";
import { clockTime } from "@/lib/labels";

export default function ReportsPage() {
  return (
    <SessionGate title="Reports" purpose="see whether your ads are paying for themselves">
      {(session) => <Reports tenantId={session.tenant_id} />}
    </SessionGate>
  );
}

function Reports({ tenantId }: { tenantId: string | null }) {
  const [report, setReport] = useState<AttributionReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    getAttributionReport(tenantId)
      .then((r) => (r.ok ? setReport(r.data) : setError(r.error)))
      .finally(() => setLoadedAt(new Date()));
  }, [tenantId]);

  const loading = !!tenantId && !loadedAt;

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        title="Reports"
        description="Are your ads paying for themselves? Every figure comes from your connected accounts. A dash means nothing was recorded, never a hidden zero."
      />

      {error ? (
        <Notice tone="offline" title="Couldn't load your report">
          {error}. The figures below show as missing until it loads. Nothing was changed.
        </Notice>
      ) : null}

      <ul className="td-stagger grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <MetricCard i={0} label="Revenue" value={report?.revenue ?? null} help="Revenue attributed to your ads" empty="No revenue recorded yet" loading={loading} />
        <MetricCard i={1} label="Ad spend" value={report?.spend ?? null} help="What your ads have cost" empty="No ad spend recorded yet" loading={loading} />
        <MetricCard
          i={2}
          label="Return on ad spend"
          value={report?.roas ?? null}
          suffix="×"
          decimals={1}
          help="Revenue ÷ ad spend"
          empty="Needs both spend and revenue"
          loading={loading}
        />
        <MetricCard i={3} label="Cost per lead" value={report?.cpl ?? null} help="Ad spend ÷ leads from ads" empty="No ad leads recorded yet" loading={loading} />
      </ul>

      <Card className="p-5 sm:p-6">
        <h2 className="text-base font-semibold text-fg">How to read this</h2>
        <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="font-medium text-fg">Return on ad spend</dt>
            <dd className="mt-1 text-fg-muted">
              For every 1 you spend on ads, how much comes back as revenue. Above 1× means the ads earn more than they cost.
            </dd>
          </div>
          <div>
            <dt className="font-medium text-fg">Cost per lead</dt>
            <dd className="mt-1 text-fg-muted">What it costs, on average, for one new person to contact you from an ad.</dd>
          </div>
        </dl>
        {loadedAt ? <p className="mt-5 text-xs text-fg-subtle">Loaded {clockTime(loadedAt)}. Amounts are in your ad account&apos;s currency.</p> : null}
      </Card>
    </div>
  );
}
