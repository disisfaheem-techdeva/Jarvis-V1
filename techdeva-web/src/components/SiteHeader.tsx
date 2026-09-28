"use client";

import Link from "next/link";
import { useState } from "react";
import { NAV, SIGNUP_URL } from "@/lib/links";

// One header for every public page: logo, menu (a drawer on phones, where the
// menu used to simply disappear), Log in, Start free trial.
export function SiteHeader() {
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-30 border-b border-white/10 bg-[var(--background)]/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-4">
        <Link href="/" className="flex items-center gap-2 text-base font-semibold text-white">
          <span className="h-2 w-2 rounded-full bg-[var(--accent)]" />
          TechDeva
        </Link>
        <nav className="hidden gap-7 text-sm text-[var(--muted)] md:flex">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className="transition-colors hover:text-white">
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="hidden items-center gap-2 text-sm md:flex">
          <Link href="/login" className="td-btn-ghost px-3 py-2">Log in</Link>
          <a href={SIGNUP_URL} className="td-btn-primary px-4 py-2">Start free trial</a>
        </div>
        <button
          type="button"
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="rounded-lg border border-white/15 px-3 py-2 text-sm text-white md:hidden"
        >
          {open ? "✕" : "☰"}
        </button>
      </div>
      {open ? (
        <div className="border-t border-white/10 px-5 pb-5 md:hidden">
          <nav className="flex flex-col py-2 text-[15px] text-[var(--muted)]">
            {NAV.map((n) => (
              <Link key={n.href} href={n.href} onClick={() => setOpen(false)} className="py-2.5 hover:text-white">
                {n.label}
              </Link>
            ))}
            <Link href="/demo" onClick={() => setOpen(false)} className="py-2.5 hover:text-white">Book a demo</Link>
          </nav>
          <div className="mt-2 grid grid-cols-2 gap-2 text-sm">
            <Link href="/login" className="td-btn-ghost px-3 py-2.5 text-center">Log in</Link>
            <a href={SIGNUP_URL} className="td-btn-primary px-3 py-2.5 text-center">Start free trial</a>
          </div>
        </div>
      ) : null}
    </header>
  );
}
