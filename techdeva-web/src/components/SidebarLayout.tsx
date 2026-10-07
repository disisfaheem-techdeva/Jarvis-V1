"use client";

import type { LucideIcon } from "lucide-react";
import { MoreHorizontal } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, ViewTransition } from "react";

import { Dialog } from "@/components/ui/Dialog";
import { Logo } from "@/components/ui/Logo";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { cn } from "@/lib/cn";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Shown in the phone bottom bar (keep to four). */
  primary?: boolean;
  /** A count that needs the owner's attention. Hidden when 0 or unknown. */
  badge?: number | null;
  group?: string;
}

function isActive(pathname: string, href: string, root: string) {
  return href === root ? pathname === root : pathname === href || pathname.startsWith(href + "/");
}

/**
 * App shell for the portal and founder console.
 * Desktop: a fixed sidebar. Phone: a slim top bar and a bottom tab bar with
 * the primary destinations plus "More", so content starts at the top of the
 * screen instead of below a stacked menu.
 */
export function SidebarLayout({
  title,
  nav,
  root,
  children,
  tone = "portal",
}: {
  title: string;
  nav: NavItem[];
  root: string;
  children: React.ReactNode;
  tone?: "portal" | "founder";
}) {
  const pathname = usePathname();
  const [moreOpen, setMoreOpen] = useState(false);
  const primary = nav.filter((n) => n.primary).slice(0, 4);
  const current = nav.find((n) => isActive(pathname, n.href, root));
  const groups = Array.from(new Set(nav.map((n) => n.group ?? "")));

  return (
    <div className="min-h-screen bg-bg lg:grid lg:grid-cols-[268px_minmax(0,1fr)]">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen flex-col border-r border-line bg-bg-elevated/80 backdrop-blur-xl lg:flex">
        <div className="flex h-16 items-center justify-between px-5">
          <Link href={root} className="rounded-md" aria-label={`${title} home`}>
            <Logo />
          </Link>
        </div>
        <p className="px-5 pb-2 text-label font-medium uppercase text-fg-subtle">
          {tone === "founder" ? (
            <span className="inline-flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-accent-ink shadow-[0_0_8px_var(--accent-glow)]" />
              {title}
            </span>
          ) : (
            title
          )}
        </p>
        <nav aria-label={title} className="flex-1 overflow-y-auto px-3 pb-4">
          {groups.map((g) => (
            <div key={g || "main"} className="mt-3 first:mt-1">
              {g ? <p className="px-3 pb-1.5 pt-2 text-xs font-medium text-fg-subtle">{g}</p> : null}
              <ul className="flex flex-col gap-0.5">
                {nav
                  .filter((n) => (n.group ?? "") === g)
                  .map((item) => (
                    <li key={item.href}>
                      <SideLink item={item} active={isActive(pathname, item.href, root)} />
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </nav>
        <div className="flex items-center justify-between border-t border-line px-5 py-3">
          <span className="text-xs text-fg-subtle">Theme</span>
          <ThemeToggle />
        </div>
      </aside>

      {/* Phone top bar */}
      <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-line bg-[color-mix(in_oklab,var(--bg)_82%,transparent)] px-4 backdrop-blur-xl lg:hidden">
        <Link href={root} aria-label={`${title} home`} className="rounded-md">
          <Logo label={current?.label ?? title} className="text-base" />
        </Link>
        <ThemeToggle />
      </header>

      <main id="main" className="min-w-0 px-4 pb-28 pt-6 sm:px-6 lg:px-10 lg:pb-16 lg:pt-10">
        <ViewTransition key={pathname} enter="td-page" exit="td-page" default="none">
          <div className="mx-auto max-w-6xl">{children}</div>
        </ViewTransition>
      </main>

      {/* Phone bottom bar */}
      <nav
        aria-label={`${title} quick links`}
        className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-[color-mix(in_oklab,var(--bg-elevated)_88%,transparent)] pb-[env(safe-area-inset-bottom)] backdrop-blur-xl lg:hidden"
      >
        <ul className="mx-auto grid max-w-lg grid-cols-5">
          {primary.map((item) => {
            const active = isActive(pathname, item.href, root);
            const Icon = item.icon;
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "relative flex h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium",
                    active ? "text-accent-ink" : "text-fg-muted",
                  )}
                >
                  <span className="relative">
                    <Icon className="h-5 w-5" aria-hidden="true" />
                    {item.badge ? (
                      <span className="absolute -right-2.5 -top-1.5 min-w-4 rounded-full bg-accent px-1 text-center text-[10px] leading-4 text-on-accent">
                        {item.badge}
                      </span>
                    ) : null}
                  </span>
                  {item.label}
                  {item.badge ? <span className="sr-only">, {item.badge} waiting</span> : null}
                </Link>
              </li>
            );
          })}
          <li>
            <button
              type="button"
              onClick={() => setMoreOpen(true)}
              aria-haspopup="dialog"
              aria-expanded={moreOpen}
              className="flex h-16 w-full flex-col items-center justify-center gap-1 text-[11px] font-medium text-fg-muted"
            >
              <MoreHorizontal className="h-5 w-5" aria-hidden="true" />
              More
            </button>
          </li>
        </ul>
      </nav>

      <Dialog open={moreOpen} onClose={() => setMoreOpen(false)} title={title} side="bottom">
        <ul className="grid grid-cols-2 gap-2">
          {nav.map((item) => {
            const Icon = item.icon;
            const active = isActive(pathname, item.href, root);
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  onClick={() => setMoreOpen(false)}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex min-h-14 items-center gap-3 rounded-[var(--p-radius-md)] border border-line px-3 text-sm",
                    active ? "border-accent-line bg-accent-tint text-fg" : "bg-surface text-fg",
                  )}
                >
                  <Icon className="h-5 w-5 shrink-0 text-accent-ink" aria-hidden="true" />
                  <span className="min-w-0 flex-1">{item.label}</span>
                  {item.badge ? (
                    <span className="rounded-full bg-accent px-1.5 text-xs text-on-accent">{item.badge}</span>
                  ) : null}
                </Link>
              </li>
            );
          })}
        </ul>
      </Dialog>
    </div>
  );
}

function SideLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group relative flex h-[var(--nav-item-h)] items-center gap-3 rounded-[var(--p-radius-sm)] px-3 text-sm transition-colors",
        active ? "bg-accent-tint font-medium text-fg" : "text-fg-muted hover:bg-surface-2 hover:text-fg",
      )}
    >
      {active ? (
        <span
          aria-hidden="true"
          className="absolute -left-3 top-2 bottom-2 w-[3px] rounded-r-full bg-accent-ink shadow-[0_0_12px_var(--accent-glow)]"
        />
      ) : null}
      <Icon className={cn("h-[18px] w-[18px] shrink-0", active ? "text-accent-ink" : "text-fg-subtle group-hover:text-fg-muted")} aria-hidden="true" />
      <span className="flex-1">{item.label}</span>
      {item.badge ? (
        <span className="rounded-full bg-accent px-2 text-xs font-medium leading-5 text-on-accent">
          {item.badge}
          <span className="sr-only"> waiting</span>
        </span>
      ) : null}
    </Link>
  );
}
