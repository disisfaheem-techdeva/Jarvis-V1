import { cn } from "@/lib/cn";

/** Placeholder block. Reserve the final size so nothing shifts on load. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("td-skeleton rounded-[var(--p-radius-sm)]", className)} />;
}

/** Announces loading to assistive tech while skeletons show visually. */
export function LoadingRegion({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}
