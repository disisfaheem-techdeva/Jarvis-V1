import { ArrowRight } from "lucide-react";
import Link from "next/link";

import { buttonClass, ButtonLink } from "@/components/ui/Button";
import { Logo } from "@/components/ui/Logo";
import { NAV, SIGNUP_URL, TRIAL_DAYS } from "@/lib/links";

export function CtaBand() {
  return (
    <section className="mx-auto w-full max-w-6xl px-4 pb-20 sm:px-6">
      <div className="td-glass td-rim relative overflow-hidden rounded-[var(--p-radius-xl)] px-6 py-12 sm:px-12 sm:py-16">
        <div aria-hidden="true" className="td-ambient pointer-events-none absolute inset-0 opacity-80" />
        <div className="relative flex flex-col gap-8 md:flex-row md:items-end md:justify-between">
          <div className="max-w-xl">
            <h2 className="font-display text-3xl font-semibold tracking-[-0.02em] text-fg sm:text-4xl">
              Run your business from one screen
            </h2>
            <p className="mt-3 text-base leading-relaxed text-fg-muted">
              Bookings, bills, customers, stock, your WhatsApp agent and your social posts, in one dashboard.
              Try it free for {TRIAL_DAYS} days, or talk to us first.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <a href={SIGNUP_URL} className={buttonClass("primary", "lg")}>
              Start free trial <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </a>
            <ButtonLink href="/demo" size="lg" variant="secondary">
              Book a demo
            </ButtonLink>
          </div>
        </div>
      </div>
    </section>
  );
}

const linkClass = "inline-flex min-h-9 items-center text-sm text-fg-muted hover:text-fg";

export function SiteFooter() {
  return (
    <footer className="border-t border-line">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-14 sm:px-6 md:grid-cols-[1.4fr_1fr_1fr]">
        <div className="max-w-sm">
          <Logo />
          <p className="mt-4 text-sm leading-relaxed text-fg-muted">
            The AI business operating system for clinics, salons and academies.
          </p>
        </div>
        <nav aria-label="Product">
          <h2 className="text-label font-medium uppercase text-fg-subtle">Product</h2>
          <ul className="mt-4 space-y-1">
            {NAV.map((n) => (
              <li key={n.href}>
                <Link href={n.href} className={linkClass}>
                  {n.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <nav aria-label="Get started">
          <h2 className="text-label font-medium uppercase text-fg-subtle">Get started</h2>
          <ul className="mt-4 space-y-1">
            <li>
              <a href={SIGNUP_URL} className={linkClass}>
                Start free trial
              </a>
            </li>
            <li>
              <Link href="/demo" className={linkClass}>
                Book a demo
              </Link>
            </li>
            <li>
              <Link href="/login" className={linkClass}>
                Log in
              </Link>
            </li>
          </ul>
        </nav>
      </div>
      <div className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-1 px-4 py-5 text-xs text-fg-subtle sm:px-6">
          <span>© {new Date().getFullYear()} TechDeva</span>
          <Link href="/privacy" className="inline-flex min-h-9 items-center hover:text-fg">
            Privacy Policy
          </Link>
          <Link href="/terms" className="inline-flex min-h-9 items-center hover:text-fg">
            Terms of Service
          </Link>
          <Link href="/data-deletion" className="inline-flex min-h-9 items-center hover:text-fg">
            Delete your data
          </Link>
        </div>
      </div>
    </footer>
  );
}
