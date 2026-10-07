import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/cn";

/** What will appear here, why it isn't here yet, and what to do next. */
export function EmptyState({
  icon: Icon,
  title,
  body,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  body?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-start gap-3 rounded-[var(--card-radius)] border border-dashed border-line-strong bg-surface/60 p-6 sm:p-8",
        className,
      )}
    >
      {Icon ? (
        <span className="inline-flex h-10 w-10 items-center justify-center rounded-[var(--p-radius-md)] bg-accent-tint text-accent-ink">
          <Icon className="h-5 w-5" aria-hidden="true" />
        </span>
      ) : null}
      <div className="max-w-prose">
        <h2 className="text-base font-semibold text-fg">{title}</h2>
        {body ? <div className="mt-1.5 text-sm leading-relaxed text-fg-muted">{body}</div> : null}
      </div>
      {action ? <div className="mt-1 flex flex-wrap gap-2">{action}</div> : null}
    </div>
  );
}
