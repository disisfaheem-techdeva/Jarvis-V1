import { ArrowRight } from "lucide-react";

import { SiteFooter } from "@/components/site/SiteFooter";
import { SiteHeader } from "@/components/site/SiteHeader";
import { ButtonLink } from "@/components/ui/Button";

/** Shared frame for marketing pages: site header, hero band, content, closing CTA, footer. */
export function MarketingPage({
  eyebrow,
  title,
  body,
  children,
  cta = true,
}: {
  eyebrow: string;
  title: string;
  body: string;
  children?: React.ReactNode;
  /** Show the closing "Book a demo" band. */
  cta?: boolean;
}) {
  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <SiteHeader />
      <main id="main" className="flex-1">
        <section className="td-ambient relative border-b border-line">
          <div className="mx-auto max-w-6xl px-4 pb-16 pt-16 sm:px-6 sm:pb-20 sm:pt-24">
            <p className="text-label font-medium uppercase text-accent-ink">{eyebrow}</p>
            <h1 className="mt-4 max-w-3xl font-display text-4xl font-semibold tracking-[-0.03em] text-fg sm:text-6xl">
              {title}
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-relaxed text-fg-muted">{body}</p>
          </div>
        </section>
        {children ? <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6 sm:py-20">{children}</div> : null}
        {cta ? <ClosingCta /> : null}
      </main>
      <SiteFooter />
    </div>
  );
}

export function ClosingCta({
  title = "See it run on a real business",
  body = "A 30-minute walkthrough on a live clinic: what your AI team does on its own, and what it brings to you for approval.",
}: {
  title?: string;
  body?: string;
}) {
  return (
    <section className="mx-auto w-full max-w-6xl px-4 pb-20 sm:px-6">
      <div className="td-glass td-rim relative overflow-hidden rounded-[var(--p-radius-xl)] px-6 py-12 sm:px-12 sm:py-16">
        <div aria-hidden="true" className="td-ambient pointer-events-none absolute inset-0 opacity-80" />
        <div className="relative flex flex-col gap-8 md:flex-row md:items-end md:justify-between">
          <div className="max-w-xl">
            <h2 className="font-display text-3xl font-semibold tracking-[-0.02em] text-fg sm:text-4xl">{title}</h2>
            <p className="mt-3 text-base leading-relaxed text-fg-muted">{body}</p>
          </div>
          <div className="flex flex-wrap gap-3">
            <ButtonLink href="/demo" size="lg">
              Book a demo <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </ButtonLink>
            <ButtonLink href="/pricing" size="lg" variant="secondary">
              How pricing works
            </ButtonLink>
          </div>
        </div>
      </div>
    </section>
  );
}
