import { cn } from "@/lib/cn";

/** Wordmark with a small lit aperture — the "violet light source". */
export function Logo({ className, label = "TechDeva" }: { className?: string; label?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5 font-display text-[17px] font-semibold tracking-tight text-fg", className)}>
      <span aria-hidden="true" className="relative inline-flex h-6 w-6 items-center justify-center rounded-[7px] bg-surface-3 ring-1 ring-line-strong">
        <span className="absolute h-2.5 w-2.5 rounded-full bg-[var(--p-violet-400)] blur-[5px]" />
        <span className="relative h-2 w-2 rounded-full bg-[var(--p-violet-300)]" />
      </span>
      {label}
    </span>
  );
}
