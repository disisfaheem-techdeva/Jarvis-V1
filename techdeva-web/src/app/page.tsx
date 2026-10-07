import {
  ArrowRight,
  BadgeCheck,
  Bot,
  CircleSlash,
  Cog,
  GraduationCap,
  Hand,
  MessageCircle,
  PlugZap,
  Scissors,
  Sparkles,
  Stethoscope,
} from "lucide-react";
import Link from "next/link";

import { ClosingCta } from "@/components/MarketingPage";
import { SiteFooter } from "@/components/site/SiteFooter";
import { SiteHeader } from "@/components/site/SiteHeader";
import { Badge } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { GlowCard } from "@/components/ui/motion";

const STEPS = [
  {
    title: "Tell it about your business",
    body: "Services, prices, opening hours and your rules. Your AI team quotes only what's on your list and refuses bookings outside your hours.",
    Icon: Cog,
  },
  {
    title: "Your AI team gets to work",
    body: "Hermes answers WhatsApp and books clients. The analyst studies your market. The campaign manager drafts ads. All of it lands in one portal.",
    Icon: Bot,
  },
  {
    title: "You approve what matters",
    body: "Anything that spends money, goes public or changes a price stops and waits for you. One tap to approve, one to reject with a reason.",
    Icon: Hand,
  },
];

const PRINCIPLES = [
  {
    title: "Not a chatbot",
    body: "Hermes is Employee #1, not the product. The platform is the operating system your whole business runs on.",
    Icon: Sparkles,
    span: "md:col-span-3",
  },
  {
    title: "Human in the loop",
    body: "Money, publishing, pricing and permissions always require your sign-off. Everything else runs on its own.",
    Icon: Hand,
    span: "md:col-span-3",
  },
  {
    title: "One engine, any industry",
    body: "Salon, clinic, academy: the same engine, configured per business. Onboarding is a config row, not new code.",
    Icon: Cog,
    span: "md:col-span-2",
  },
  {
    title: "Null, never zero",
    body: "A number with no data behind it shows as missing. You'll never see a made-up 0 dressed up as a result.",
    Icon: CircleSlash,
    span: "md:col-span-2",
  },
  {
    title: "Connections you can verify",
    body: "A connection only shows as live when a working credential is stored, with the last time it was checked.",
    Icon: PlugZap,
    span: "md:col-span-2",
  },
];

const INDUSTRIES = [
  { label: "Aesthetic clinics", Icon: Stethoscope },
  { label: "Salons", Icon: Scissors },
  { label: "Academies", Icon: GraduationCap },
  { label: "Any appointment business", Icon: MessageCircle },
];

export default function Home() {
  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <SiteHeader />
      <main id="main" className="flex-1">
        {/* Hero */}
        <section className="relative overflow-hidden border-b border-line">
          <div aria-hidden="true" className="td-ambient pointer-events-none absolute inset-0" />
          <div
            aria-hidden="true"
            className="pointer-events-none absolute left-1/2 top-[-30%] h-[640px] w-[900px] -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,var(--accent-glow),transparent)] opacity-60 blur-2xl"
          />
          <div className="relative mx-auto grid max-w-6xl gap-14 px-4 pb-16 pt-14 sm:px-6 sm:pt-20 lg:grid-cols-[1.15fr_1fr] lg:items-center lg:pb-24 lg:pt-24">
            <div className="td-stagger min-w-0">
              <Link
                href="/case-studies"
                style={{ "--i": 0 } as React.CSSProperties}
                className="inline-flex max-w-full items-center gap-2 rounded-full border border-line-strong bg-surface/70 py-1.5 pl-2 pr-3 text-xs text-fg-muted transition-colors hover:text-fg"
              >
                <Badge tone="success" dot className="h-5 px-2">
                  Live
                </Badge>
                <span className="truncate">In production at The Beauty Aesthetic &amp; Lounge, Lahore</span>
                <ArrowRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              </Link>
              <h1
                style={{ "--i": 1 } as React.CSSProperties}
                className="mt-7 font-display text-display font-semibold text-fg"
              >
                The AI Business{" "}
                <span className="bg-[linear-gradient(100deg,var(--fg)_20%,var(--accent-ink)_75%)] bg-clip-text text-transparent">
                  Operating System
                </span>
              </h1>
              <p style={{ "--i": 2 } as React.CSSProperties} className="mt-6 max-w-xl text-lg leading-relaxed text-fg-muted">
                One AI workforce for your leads, bookings, campaigns and reports. It runs on its own and
                stops for your approval before anything costs money or goes public.
              </p>
              <div style={{ "--i": 3 } as React.CSSProperties} className="mt-9 flex flex-wrap gap-3">
                <ButtonLink href="/demo" size="lg">
                  Book a demo <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </ButtonLink>
                <ButtonLink href="#how" size="lg" variant="secondary">
                  See how it works
                </ButtonLink>
              </div>
            </div>

            <ProductPreview />
          </div>
        </section>

        {/* Built for */}
        <section aria-labelledby="built-for" className="border-b border-line">
          <div className="mx-auto flex max-w-6xl flex-col gap-5 px-4 py-8 sm:px-6 md:flex-row md:items-center md:justify-between">
            <h2 id="built-for" className="text-label font-medium uppercase text-fg-subtle">
              Built for service businesses
            </h2>
            <ul className="flex flex-wrap gap-x-8 gap-y-3">
              {INDUSTRIES.map(({ label, Icon }) => (
                <li key={label} className="flex items-center gap-2 text-sm text-fg-muted">
                  <Icon className="h-4 w-4 text-accent-ink" aria-hidden="true" />
                  {label}
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* How it works */}
        <section id="how" aria-labelledby="how-title" className="scroll-mt-20">
          <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6 sm:py-28">
            <div className="max-w-2xl">
              <p className="text-label font-medium uppercase text-accent-ink">How it works</p>
              <h2 id="how-title" className="mt-3 font-display text-3xl font-semibold tracking-[-0.02em] text-fg sm:text-5xl">
                Your business, run by an AI team that asks first
              </h2>
            </div>
            <ol className="mt-14 grid gap-px overflow-hidden rounded-[var(--p-radius-xl)] border border-line bg-line md:grid-cols-3">
              {STEPS.map(({ title, body, Icon }, i) => (
                <li key={title} className="flex flex-col gap-4 bg-surface p-7 sm:p-8">
                  <div className="flex items-center justify-between">
                    <span className="inline-flex h-11 w-11 items-center justify-center rounded-[var(--p-radius-md)] bg-accent-tint text-accent-ink">
                      <Icon className="h-5 w-5" aria-hidden="true" />
                    </span>
                    <span className="font-mono text-xs text-fg-subtle">Step {i + 1}</span>
                  </div>
                  <h3 className="text-lg font-semibold text-fg">{title}</h3>
                  <p className="text-[15px] leading-relaxed text-fg-muted">{body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* Principles */}
        <section aria-labelledby="principles-title" className="border-y border-line bg-bg-elevated">
          <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6 sm:py-28">
            <div className="max-w-2xl">
              <p className="text-label font-medium uppercase text-accent-ink">What you can count on</p>
              <h2 id="principles-title" className="mt-3 font-display text-3xl font-semibold tracking-[-0.02em] text-fg sm:text-5xl">
                We show what can be proven, not a green tick
              </h2>
            </div>
            <ul className="mt-14 grid gap-4 md:grid-cols-6">
              {PRINCIPLES.map(({ title, body, Icon, span }) => (
                <GlowCard as="li" key={title} className={`rounded-[var(--card-radius)] border border-line bg-surface p-7 ${span}`}>
                  <Icon className="h-5 w-5 text-accent-ink" aria-hidden="true" />
                  <h3 className="mt-5 text-lg font-semibold text-fg">{title}</h3>
                  <p className="mt-2 text-[15px] leading-relaxed text-fg-muted">{body}</p>
                </GlowCard>
              ))}
            </ul>
          </div>
        </section>

        {/* Proof */}
        <section aria-labelledby="proof-title">
          <div className="mx-auto grid max-w-6xl gap-10 px-4 py-20 sm:px-6 sm:py-28 lg:grid-cols-2 lg:items-center">
            <div>
              <p className="text-label font-medium uppercase text-accent-ink">Proof</p>
              <h2 id="proof-title" className="mt-3 font-display text-3xl font-semibold tracking-[-0.02em] text-fg sm:text-5xl">
                Running a real clinic before it runs yours
              </h2>
              <p className="mt-5 max-w-xl text-[17px] leading-relaxed text-fg-muted">
                TechDeva runs in production at The Beauty Aesthetic &amp; Lounge in Gulberg, Lahore. We publish
                results once they are real and measured, never as a projection dressed up as a result.
              </p>
              <Link
                href="/case-studies"
                className="mt-7 inline-flex min-h-11 items-center gap-2 text-sm font-medium text-accent-ink hover:underline"
              >
                Read about the case study <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
            </div>
            <dl className="grid gap-4 sm:grid-cols-2">
              {[
                ["Status", "Live in production"],
                ["Business", "Aesthetic clinic, salon and academy"],
                ["AI employee #1", "Hermes on WhatsApp"],
                ["Approval gate", "On for money, publishing and pricing"],
              ].map(([k, v]) => (
                <div key={k} className="rounded-[var(--card-radius)] border border-line bg-surface p-5">
                  <dt className="text-label font-medium uppercase text-fg-subtle">{k}</dt>
                  <dd className="mt-2 flex items-center gap-2 text-base font-medium text-fg">
                    {k === "Status" ? <BadgeCheck className="h-4 w-4 text-success" aria-hidden="true" /> : null}
                    {v}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        <ClosingCta />
      </main>
      <SiteFooter />
    </div>
  );
}

/** An illustration of the portal's approval gate built from real UI primitives. */
function ProductPreview() {
  return (
    <figure className="relative mx-auto w-full max-w-lg lg:max-w-none" aria-labelledby="preview-caption">
      <div className="td-glass td-rim relative rounded-[var(--p-radius-xl)] p-4 sm:p-5">
        <div className="flex items-center justify-between px-1 pb-4">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-surface-3" />
            <span className="h-2.5 w-2.5 rounded-full bg-surface-3" />
            <span className="h-2.5 w-2.5 rounded-full bg-surface-3" />
          </div>
          <span className="text-xs text-fg-subtle">Client portal · Approvals</span>
        </div>

        <div className="rounded-[var(--card-radius)] border border-line bg-surface p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs text-fg-subtle">Campaign manager asks</p>
              <p className="mt-1 font-medium text-fg">Launch a WhatsApp ad for Hair PRP in Gulberg</p>
            </div>
            <Badge tone="warning" dot>
              Waiting
            </Badge>
          </div>
          <dl className="mt-5 grid grid-cols-3 gap-3 border-y border-line py-4">
            {[
              ["Budget", "15,000"],
              ["Runs", "7 days"],
              ["Reach", "8 km"],
            ].map(([k, v]) => (
              <div key={k}>
                <dt className="text-xs text-fg-subtle">{k}</dt>
                <dd className="mt-1 font-display text-xl font-semibold tabular-nums text-fg">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-4 flex gap-2" aria-hidden="true">
            <span className="inline-flex h-10 flex-1 items-center justify-center rounded-[var(--btn-radius)] bg-success-fill text-sm font-medium text-white">
              Approve
            </span>
            <span className="inline-flex h-10 flex-1 items-center justify-center rounded-[var(--btn-radius)] border border-line-strong text-sm font-medium text-fg-muted">
              Reject
            </span>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-3">
          <div className="rounded-[var(--card-radius)] border border-line bg-surface p-4">
            <p className="text-xs text-fg-subtle">New leads today</p>
            <p className="mt-1 font-display text-3xl font-semibold tabular-nums text-fg">4</p>
          </div>
          <div className="rounded-[var(--card-radius)] border border-line bg-surface p-4">
            <p className="text-xs text-fg-subtle">Return on ad spend</p>
            <p className="mt-1 font-display text-3xl font-semibold text-fg-subtle">—</p>
            <p className="text-xs text-fg-subtle">No spend yet</p>
          </div>
        </div>
      </div>
      <figcaption id="preview-caption" className="mt-3 text-center text-xs text-fg-subtle">
        Illustration with example figures
      </figcaption>
    </figure>
  );
}
