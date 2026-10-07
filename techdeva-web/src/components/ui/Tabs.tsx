"use client";

import { useRef } from "react";

import { cn } from "@/lib/cn";

export interface TabItem {
  value: string;
  label: string;
  count?: number;
}

/**
 * Controlled tablist with roving focus (WAI-ARIA tabs pattern): arrow keys move
 * between tabs, Home/End jump to the ends. Pair with <TabPanel>.
 */
export function Tabs({
  id: base,
  items,
  value,
  onChange,
  label,
  className,
}: {
  /** Shared with <TabPanel tabsId> so tabs and panels reference each other. */
  id: string;
  items: TabItem[];
  value: string;
  onChange: (value: string) => void;
  label: string;
  className?: string;
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  function onKeyDown(e: React.KeyboardEvent, index: number) {
    let next = index;
    if (e.key === "ArrowRight") next = (index + 1) % items.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + items.length) % items.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;
    else return;
    e.preventDefault();
    onChange(items[next].value);
    refs.current[next]?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn("inline-flex max-w-full gap-1 overflow-x-auto rounded-[var(--p-radius-md)] border border-line bg-surface p-1", className)}
    >
      {items.map((t, i) => {
        const selected = t.value === value;
        return (
          <button
            key={t.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            type="button"
            id={`${base}-tab-${t.value}`}
            aria-selected={selected}
            aria-controls={selected ? `${base}-panel-${t.value}` : undefined}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(t.value)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={cn(
              "inline-flex h-9 items-center gap-2 whitespace-nowrap rounded-[var(--p-radius-sm)] px-3 text-sm font-medium transition-colors",
              selected ? "bg-surface-3 text-fg" : "text-fg-muted hover:text-fg",
            )}
          >
            {t.label}
            {typeof t.count === "number" ? (
              <span className="rounded-full bg-bg px-1.5 text-xs tabular-nums text-fg-muted">{t.count}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** The panel for one tab. Render only the selected one. */
export function TabPanel({
  tabsId,
  value,
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { tabsId: string; value: string }) {
  return (
    <div
      role="tabpanel"
      id={`${tabsId}-panel-${value}`}
      aria-labelledby={`${tabsId}-tab-${value}`}
      tabIndex={0}
      className={cn("rounded-[var(--p-radius-sm)] focus-visible:outline-none focus-visible:[box-shadow:var(--focus-ring)]", className)}
      {...props}
    />
  );
}
