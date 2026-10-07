import type { Metadata } from "next";
import { ArrowRight, Check, ChevronDown, Hand } from "lucide-react";

import { MarketingPage } from "@/components/MarketingPage";
import { ButtonLink } from "@/components/ui/Button";
import { GlowCard } from "@/components/ui/motion";

export const metadata: Metadata = { title: "Pricing" };

const INCLUDED = [
  "Hermes, your AI sales agent on WhatsApp, answering and booking from your own price list",
  "An analyst that researches your market and competitors",
  "A campaign manager that drafts ads, which wait for your approval",
  "Reporting on leads, ad spend and return, with nothing filled in by guesswork",
  "A client portal with your approvals, leads and connections",
  "Setup done with you: services, prices, hours and policies",
];

const STEPS = [
  {
    title: "Book a 30-minute demo",
    body: "We walk through the platform running on a live clinic, then look at how your business works today.",
  },
  {
    title: "Get a proposal for your business",
    body: "What your AI team will handle, what will always come to you for approval, and the price for that scope.",
  },
  {
    title: "Go live with the gate on",
    body: "We set up your business details and connections together. From day one, nothing is spent or published without you.",
  },
];

const GATED = ["Spending money on ads", "Publishing posts and campaigns", "Changing your prices", "Granting new permissions"];

const FAQ = [
  {
    q: "Why isn't there a price list?",
    a: "Pricing is set per engagement while the platform is proven on its first paying clients. A clinic with one branch and a salon with five need different setups, and we'd rather quote what you actually need than publish a number that fits nobody.",
  },
  {
    q: "Can my AI team spend money without asking?",
    a: "No. Every campaign shows its budget and waits on your Approvals screen. The gate is enforced in the platform itself, not only in the screen you see.",
  },
  {
    q: "What do I need to get started?",
    a: "Your list of services with prices, your opening hours and your policies (deposits, cancellations). We set up WhatsApp and Meta with you.",
  },
  {
    q: "Can I see it working before I decide?",
    a: "Yes. The demo runs on a real clinic in production, not a slide deck.",
  },
];

export default function PricingPage() {
  return (
    <MarketingPage
      eyebrow="Pricing"
      ctaSecondary={{ href: "/platform", label: "Explore the platform" }}
      title="Priced for your business, after we've seen it"
      body="Pricing is a founder decision, set per engagement while the platform is proven on its first paying clients. Book a demo and we'll walk through what fits your business."
    >
      <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
        <GlowCard className="td-glass td-rim rounded-[var(--p-radius-xl)] p-7 sm:p-9">
          <p className="text-label font-medium uppercase text-accent-ink">Every engagement includes</p>
          <h2 className="mt-3 font-display text-2xl font-semibold tracking-tight text-fg sm:text-3xl">
            One AI workforce, set up around your business
          </h2>
          <ul className="mt-7 grid gap-3.5">
            {INCLUDED.map((item) => (
              <li key={item} className="flex gap-3 text-[15px] leading-relaxed text-fg">
                <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent-tint text-accent-ink">
                  <Check className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                {item}
              </li>
            ))}
          </ul>
          <div className="mt-9 flex flex-wrap items-center gap-4 border-t border-line pt-7">
            <ButtonLink href="/demo" size="lg">
              Book a demo <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </ButtonLink>
            <p className="text-sm text-fg-muted">30 minutes · no commitment</p>
          </div>
        </GlowCard>

        <div className="flex flex-col gap-6">
          <section aria-labelledby="gate-title" className="rounded-[var(--p-radius-xl)] border border-line bg-surface p-7">
            <span className="inline-flex h-10 w-10 items-center justify-center rounded-[var(--p-radius-md)] bg-warning-tint text-warning">
              <Hand className="h-5 w-5" aria-hidden="true" />
            </span>
            <h2 id="gate-title" className="mt-5 text-lg font-semibold text-fg">
              Always your decision
            </h2>
            <p className="mt-2 text-[15px] text-fg-muted">At every price, these stop and wait for your approval:</p>
            <ul className="mt-4 grid gap-2">
              {GATED.map((g) => (
                <li key={g} className="flex items-center gap-2.5 text-[15px] text-fg">
                  <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-warning" />
                  {g}
                </li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="steps-title" className="rounded-[var(--p-radius-xl)] border border-line bg-surface p-7">
            <h2 id="steps-title" className="text-lg font-semibold text-fg">
              How an engagement is set
            </h2>
            <ol className="mt-5 grid gap-5">
              {STEPS.map((s, i) => (
                <li key={s.title} className="grid grid-cols-[2rem_1fr] gap-3">
                  <span className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-line-strong font-mono text-xs text-fg-muted">
                    {i + 1}
                  </span>
                  <div>
                    <h3 className="font-medium text-fg">{s.title}</h3>
                    <p className="mt-1 text-sm leading-relaxed text-fg-muted">{s.body}</p>
                  </div>
                </li>
              ))}
            </ol>
          </section>
        </div>
      </div>

      <section aria-labelledby="faq-title" className="mt-20 grid gap-8 lg:grid-cols-[1fr_1.6fr]">
        <div>
          <p className="text-label font-medium uppercase text-accent-ink">Questions</p>
          <h2 id="faq-title" className="mt-3 font-display text-3xl font-semibold tracking-[-0.02em] text-fg sm:text-4xl">
            Before you book
          </h2>
        </div>
        <div className="divide-y divide-line border-y border-line">
          {FAQ.map((f) => (
            <details key={f.q} className="group">
              <summary className="flex min-h-14 cursor-pointer list-none items-center justify-between gap-4 py-4 text-base font-medium text-fg [&::-webkit-details-marker]:hidden">
                {f.q}
                <ChevronDown
                  className="h-5 w-5 shrink-0 text-fg-subtle transition-transform group-open:rotate-180"
                  aria-hidden="true"
                />
              </summary>
              <p className="pb-5 pr-8 text-[15px] leading-relaxed text-fg-muted">{f.a}</p>
            </details>
          ))}
        </div>
      </section>
    </MarketingPage>
  );
}
