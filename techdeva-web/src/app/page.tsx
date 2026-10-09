import type { Metadata } from "next";
import {
  ArrowRight,
  BadgeCheck,
  BarChart3,
  CalendarCheck,
  Mic,
  MessageCircle,
  Rocket,
  Share2,
  Users,
  UsersRound,
  Wallet,
} from "lucide-react";
import Link from "next/link";

import { CtaBand, SiteFooter } from "@/components/SiteFooter";
import { SiteHeader } from "@/components/SiteHeader";
import { Badge } from "@/components/ui/Badge";
import { buttonClass, ButtonLink } from "@/components/ui/Button";
import { GlowCard } from "@/components/ui/motion";
import { SIGNUP_URL, TRIAL_DAYS } from "@/lib/links";

const FEATURES = [
  { title: "Appointments & bills", body: "Book clients with services, discounts, GST and part payments. Every balance comes from the real bill.", Icon: CalendarCheck },
  { title: "Customers & history", body: "Every visit, payment, package, note and WhatsApp chat for each customer, in one place.", Icon: Users },
  { title: "WhatsApp AI agent", body: "Your own agent answers customers on your business number in their language, and you can reply from the dashboard.", Icon: MessageCircle },
  { title: "Jarvis, your voice assistant", body: "Ask about your figures, change prices or hours, or attach a photo and have a post drafted, just by talking.", Icon: Mic },
  { title: "Money & stock", body: "Expenses, daily closing, profit and loss, and stock that adds up from every movement.", Icon: Wallet },
  { title: "Team & permissions", body: "Staff, attendance and HR. Each person logs in with their own PIN; front desk can book and take payments, prices and deletions stay with the owner.", Icon: UsersRound },
  { title: "Facebook & Instagram posting", body: "Write or dictate a post, add your photo and publish or schedule it to your Page and Instagram from the dashboard.", Icon: Share2 },
  { title: "Live ad results", body: "Spend, leads, WhatsApp chats and cost per result from your Meta and Google ads, next to the bookings they brought.", Icon: BarChart3 },
  { title: "Growth advisor & ad autopilot", body: "Every week it reads your numbers and says what to fix and what to do next. It can pause ads that cost too much and write new campaigns, within limits you set.", Icon: Rocket },
];

const STEPS = [
  ["Sign up", "Your business and your owner login are created straight away."],
  ["Tell it about your business", "Services, prices, hours and your logo. Your dashboard uses them immediately."],
  ["Connect WhatsApp & Facebook", "Scan a QR code for WhatsApp, and connect your Page to post from the dashboard."],
] as const;

export const metadata: Metadata = {
  title: { absolute: "TechDeva: clinic & salon software with a WhatsApp AI agent" },
  description:
    "The AI business operating system for clinics, salons, gyms and academies in Pakistan and abroad: bookings, bills, customers, " +
    "stock, team, a WhatsApp AI agent that answers and books 24/7, social posting, ad results and a growth advisor. 7-day free trial.",
  alternates: { canonical: "/" },
};

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
                <span className="truncate">In production — The Beauty Aesthetic &amp; Lounge</span>
                <ArrowRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              </Link>
              <h1 style={{ "--i": 1 } as React.CSSProperties} className="mt-7 font-display text-display font-semibold text-fg">
                The AI Business{" "}
                <span className="bg-[linear-gradient(100deg,var(--fg)_20%,var(--accent-ink)_75%)] bg-clip-text text-transparent">
                  Operating System
                </span>
              </h1>
              <p style={{ "--i": 2 } as React.CSSProperties} className="mt-6 max-w-xl text-lg leading-relaxed text-fg-muted">
                One dashboard for your clinic, salon or academy: bookings, bills, customers, stock, your team, a WhatsApp AI
                agent, social posting, ad results and a growth advisor. The AI does the routine work; you set the limits and
                approve what matters.
              </p>
              <div style={{ "--i": 3 } as React.CSSProperties} className="mt-9 flex flex-wrap gap-3">
                <a href={SIGNUP_URL} className={buttonClass("primary", "lg")}>
                  Start your {TRIAL_DAYS}-day free trial <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </a>
                <ButtonLink href="/demo" size="lg" variant="secondary">
                  Book a demo
                </ButtonLink>
              </div>
              <p style={{ "--i": 4 } as React.CSSProperties} className="mt-3 text-sm text-fg-subtle">
                No card needed. Set up takes a few minutes.
              </p>
            </div>

            <ProductPreview />
          </div>
        </section>

        {/* Features */}
        <section aria-labelledby="features-title">
          <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6 sm:py-28">
            <div className="max-w-2xl">
              <p className="text-label font-medium uppercase text-accent-ink">One dashboard</p>
              <h2 id="features-title" className="mt-3 font-display text-3xl font-semibold tracking-[-0.02em] text-fg sm:text-5xl">
                Everything your front desk, your books and your marketing need
              </h2>
            </div>
            <ul className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map(({ title, body, Icon }) => (
                <GlowCard as="li" key={title} className="rounded-[var(--card-radius)] border border-line bg-surface p-7">
                  <span className="inline-flex h-11 w-11 items-center justify-center rounded-[var(--p-radius-md)] bg-accent-tint text-accent-ink">
                    <Icon className="h-5 w-5" aria-hidden="true" />
                  </span>
                  <h3 className="mt-5 text-lg font-semibold text-fg">{title}</h3>
                  <p className="mt-2 text-[15px] leading-relaxed text-fg-muted">{body}</p>
                </GlowCard>
              ))}
            </ul>
          </div>
        </section>

        {/* How it starts */}
        <section aria-labelledby="how-title" className="border-y border-line bg-bg-elevated">
          <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6 sm:py-28">
            <div className="max-w-2xl">
              <p className="text-label font-medium uppercase text-accent-ink">How it starts</p>
              <h2 id="how-title" className="mt-3 font-display text-3xl font-semibold tracking-[-0.02em] text-fg sm:text-5xl">
                Running on your business in minutes
              </h2>
            </div>
            <ol className="mt-14 grid gap-px overflow-hidden rounded-[var(--p-radius-xl)] border border-line bg-line md:grid-cols-3">
              {STEPS.map(([title, body], i) => (
                <li key={title} className="flex flex-col gap-4 bg-surface p-7 sm:p-8">
                  <span className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-line-strong font-mono text-sm text-fg-muted">
                    {i + 1}
                  </span>
                  <h3 className="text-lg font-semibold text-fg">{title}</h3>
                  <p className="text-[15px] leading-relaxed text-fg-muted">{body}</p>
                </li>
              ))}
            </ol>
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
                TechDeva runs in production at The Beauty Aesthetic &amp; Lounge in Lahore. We publish results once they are
                real and measured, never as a projection dressed up as a result.
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
                ["AI employee #1", "WhatsApp sales agent"],
                ["You stay in control", "Prices, deletions and spend limits stay with the owner"],
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

        <CtaBand />
      </main>
      <SiteFooter />
    </div>
  );
}

/** An illustration of the dashboard built from real UI primitives, clearly marked as example figures. */
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
          <span className="text-xs text-fg-subtle">Your dashboard · Today</span>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-[var(--card-radius)] border border-line bg-surface p-4">
            <p className="text-xs text-fg-subtle">Bookings today</p>
            <p className="mt-1 font-display text-3xl font-semibold tabular-nums text-fg">12</p>
          </div>
          <div className="rounded-[var(--card-radius)] border border-line bg-surface p-4">
            <p className="text-xs text-fg-subtle">WhatsApp chats answered</p>
            <p className="mt-1 font-display text-3xl font-semibold tabular-nums text-fg">38</p>
          </div>
        </div>

        <div className="mt-3 rounded-[var(--card-radius)] border border-line bg-surface p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs text-fg-subtle">Your AI agent drafted a post</p>
              <p className="mt-1 font-medium text-fg">HydraFacial: glow in an hour. Book on WhatsApp.</p>
            </div>
            <Badge tone="warning" dot>
              Waiting
            </Badge>
          </div>
          <div className="mt-4 flex gap-2" aria-hidden="true">
            <span className="inline-flex h-10 flex-1 items-center justify-center rounded-[var(--btn-radius)] bg-success-fill text-sm font-medium text-white">
              Approve
            </span>
            <span className="inline-flex h-10 flex-1 items-center justify-center rounded-[var(--btn-radius)] border border-line-strong text-sm font-medium text-fg-muted">
              Edit
            </span>
          </div>
        </div>
      </div>
      <figcaption id="preview-caption" className="mt-3 text-center text-xs text-fg-subtle">
        Illustration with example figures
      </figcaption>
    </figure>
  );
}
