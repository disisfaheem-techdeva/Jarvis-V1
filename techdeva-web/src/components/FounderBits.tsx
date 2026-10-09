"use client";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";

/** Mission-control header: live-signal dot, title, and what the screen watches. */
export function ConsoleHeader({ title, description, live }: { title: string; description: string; live?: boolean | null }) {
  return (
    <header className="relative overflow-hidden rounded-[var(--p-radius-xl)] border border-line bg-bg-elevated px-6 py-7 sm:px-8">
      <div aria-hidden="true" className="td-ambient pointer-events-none absolute inset-0 opacity-70" />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-[0.07] [background-image:linear-gradient(var(--fg)_1px,transparent_1px),linear-gradient(90deg,var(--fg)_1px,transparent_1px)] [background-size:32px_32px]"
      />
      <div className="relative flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.14em] text-accent-ink">Founder console</p>
          <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight text-fg sm:text-4xl">{title}</h1>
          <p className="mt-2 max-w-2xl text-[15px] text-fg-muted">{description}</p>
        </div>
        {live === undefined ? null : (
          <Badge tone={live ? "success" : live === false ? "danger" : "neutral"} dot>
            {live ? "Live data" : live === false ? "No live data" : "Connecting…"}
          </Badge>
        )}
      </div>
    </header>
  );
}

export function severityTone(severity: string): BadgeTone {
  const s = severity?.toLowerCase();
  if (s === "critical" || s === "high") return "danger";
  if (s === "medium" || s === "warning") return "warning";
  if (s === "low" || s === "info") return "info";
  return "neutral";
}

/** A thin vertical stripe that encodes severity at a glance. */
export function SeverityStripe({ tone }: { tone: BadgeTone }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "absolute inset-y-0 left-0 w-1",
        tone === "danger" && "bg-danger",
        tone === "warning" && "bg-warning",
        tone === "info" && "bg-info",
        tone === "neutral" && "bg-line-strong",
        (tone === "success" || tone === "accent") && "bg-success",
      )}
    />
  );
}
