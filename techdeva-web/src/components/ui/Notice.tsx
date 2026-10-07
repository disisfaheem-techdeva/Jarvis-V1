import { AlertTriangle, CircleCheck, Info, WifiOff } from "lucide-react";

import { cn } from "@/lib/cn";

type Tone = "info" | "success" | "warning" | "danger" | "offline";

const styles: Record<Tone, { wrap: string; Icon: typeof Info }> = {
  info: { wrap: "border-info/30 bg-info-tint text-fg", Icon: Info },
  success: { wrap: "border-success/30 bg-success-tint text-fg", Icon: CircleCheck },
  warning: { wrap: "border-warning/30 bg-warning-tint text-fg", Icon: AlertTriangle },
  danger: { wrap: "border-danger/30 bg-danger-tint text-fg", Icon: AlertTriangle },
  offline: { wrap: "border-line-strong bg-surface-2 text-fg", Icon: WifiOff },
};

const iconInk: Record<Tone, string> = {
  info: "text-info",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  offline: "text-fg-muted",
};

/** An inline message with an optional action. Used for errors, warnings and confirmations. */
export function Notice({
  tone = "info",
  title,
  children,
  action,
  className,
  live,
}: {
  tone?: Tone;
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  /** Announce when it appears (use for results of an action). */
  live?: boolean;
}) {
  const { wrap, Icon } = styles[tone];
  return (
    <div
      role={live ? (tone === "danger" ? "alert" : "status") : undefined}
      className={cn("flex flex-col gap-3 rounded-[var(--p-radius-md)] border p-4 sm:flex-row sm:items-start", wrap, className)}
    >
      <Icon className={cn("mt-0.5 h-5 w-5 shrink-0", iconInk[tone])} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">{title}</p>
        {children ? <div className="mt-1 text-sm text-fg-muted">{children}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}
