"use client";

import { Card } from "@/components/ui/Card";
import { CountUp } from "@/components/ui/motion";
import { Skeleton } from "@/components/ui/Skeleton";

/**
 * A headline number. Missing data renders as a dash with a reason, never 0
 * (Constitution invariant #2). Counts up once on view; static under reduced motion.
 * Render inside a list (it is an <li>), usually a `.td-stagger` grid.
 */
export function MetricCard({
  i = 0,
  label,
  value,
  suffix,
  decimals = 0,
  help,
  empty = "No data recorded yet",
  loading,
}: {
  i?: number;
  label: string;
  value: number | null;
  suffix?: string;
  decimals?: number;
  help: string;
  empty?: string;
  loading: boolean;
}) {
  return (
    <li style={{ "--i": i } as React.CSSProperties}>
      <Card className="flex h-full flex-col p-4 sm:p-6">
        <p className="text-[13px] font-medium text-fg-muted sm:text-sm">{label}</p>
        {loading ? (
          <Skeleton className="mt-4 h-12 w-24" />
        ) : value == null ? (
          <p className="mt-3 font-display text-metric font-semibold text-fg-subtle">
            <span aria-hidden="true">—</span>
            <span className="sr-only">No data</span>
          </p>
        ) : (
          <p className="mt-3 font-display text-metric font-semibold text-fg">
            <CountUp
              value={value}
              delay={i * 90}
              format={(n) => n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}
            />
            {suffix ? <span className="ml-1 text-2xl text-fg-muted">{suffix}</span> : null}
          </p>
        )}
        <p className="mt-auto pt-3 text-xs text-fg-subtle">{value == null && !loading ? empty : help}</p>
      </Card>
    </li>
  );
}
