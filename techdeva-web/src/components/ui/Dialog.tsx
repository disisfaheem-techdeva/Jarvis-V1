"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef } from "react";

import { cn } from "@/lib/cn";

/**
 * Modal dialog on the native <dialog> element: focus trapping, Escape to close
 * and the inert background come from the browser, not from a dependency.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  className,
  side,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children?: React.ReactNode;
  className?: string;
  /** Render as a sheet from the bottom (mobile menus) instead of centred. */
  side?: "bottom";
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descId = useId();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={description ? descId : undefined}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      className={cn(
        "m-auto w-[min(100%-2rem,32rem)] rounded-[var(--card-radius)] border border-line-strong bg-bg-elevated p-0 text-fg shadow-[var(--shadow-lg)]",
        "backdrop:bg-[var(--overlay)] backdrop:backdrop-blur-sm open:animate-rise",
        side === "bottom" &&
          "mb-0 w-full max-w-none rounded-b-none pb-[env(safe-area-inset-bottom)] sm:mb-auto sm:w-[min(100%-2rem,32rem)] sm:rounded-b-[var(--card-radius)]",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-4 p-5 pb-0 sm:p-6 sm:pb-0">
        <div>
          <h2 id={titleId} className="text-lg font-semibold tracking-tight">
            {title}
          </h2>
          {description ? (
            <p id={descId} className="mt-1 text-sm text-fg-muted">
              {description}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="-mr-2 -mt-1 inline-flex h-11 w-11 items-center justify-center rounded-[var(--btn-radius)] text-fg-muted hover:bg-surface-2 hover:text-fg"
        >
          <X className="h-5 w-5" aria-hidden="true" />
        </button>
      </div>
      <div className="p-5 sm:p-6">{children}</div>
    </dialog>
  );
}
