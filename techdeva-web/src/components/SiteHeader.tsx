"use client";

import { Menu } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { buttonClass, ButtonLink } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Logo } from "@/components/ui/Logo";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { cn } from "@/lib/cn";
import { NAV, SIGNUP_URL } from "@/lib/links";

// One header for every public page: logo, menu (a sheet on phones, where the
// menu used to simply disappear), Log in, Start free trial.
export function SiteHeader() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-[color-mix(in_oklab,var(--bg)_78%,transparent)] backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <Link href="/" aria-label="TechDeva home" className="rounded-md">
          <Logo />
        </Link>

        <nav aria-label="Main" className="hidden items-center gap-1 lg:flex">
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              aria-current={pathname === n.href ? "page" : undefined}
              className={cn(
                "rounded-md px-3 py-2 text-sm transition-colors",
                pathname === n.href ? "text-fg" : "text-fg-muted hover:text-fg",
              )}
            >
              {n.label}
            </Link>
          ))}
        </nav>

        <div className="flex items-center gap-1 sm:gap-2">
          <div className="hidden items-center gap-1 sm:flex">
            <ThemeToggle />
            <ButtonLink href="/login" variant="ghost" size="sm">
              Log in
            </ButtonLink>
          </div>
          <a href={SIGNUP_URL} className={buttonClass("primary", "sm")}>
            Start free trial
          </a>
          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-label="Open menu"
            className="inline-flex h-11 w-11 items-center justify-center rounded-[var(--btn-radius)] text-fg hover:bg-surface-2 lg:hidden"
          >
            <Menu className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>
      </div>

      <Dialog open={open} onClose={() => setOpen(false)} title="Menu" side="bottom">
        <nav aria-label="Main" className="flex flex-col">
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              onClick={() => setOpen(false)}
              aria-current={pathname === n.href ? "page" : undefined}
              className="flex h-12 items-center border-b border-line text-base text-fg aria-[current=page]:text-accent-ink"
            >
              {n.label}
            </Link>
          ))}
          <Link href="/demo" onClick={() => setOpen(false)} className="flex h-12 items-center border-b border-line text-base text-fg">
            Book a demo
          </Link>
        </nav>
        <div className="mt-5 grid grid-cols-2 gap-2">
          <ButtonLink href="/login" variant="secondary" onClick={() => setOpen(false)}>
            Log in
          </ButtonLink>
          <a href={SIGNUP_URL} className={buttonClass("primary", "md")}>
            Start free trial
          </a>
        </div>
        <div className="mt-3 flex justify-end">
          <ThemeToggle />
        </div>
      </Dialog>
    </header>
  );
}
