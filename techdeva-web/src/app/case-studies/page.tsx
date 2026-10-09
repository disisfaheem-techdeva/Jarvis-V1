import type { Metadata } from "next";
import { BadgeCheck, FileClock, Ruler } from "lucide-react";

import { MarketingPage } from "@/components/MarketingPage";
import { Badge } from "@/components/ui/Badge";

export const metadata: Metadata = {
  title: "Case studies",
  description: "TechDeva in production at The Beauty Aesthetic & Lounge, a doctor-led aesthetic clinic in Lahore. Real numbers, published once they are real.",
  alternates: { canonical: "/case-studies" },
};

export default function CaseStudiesPage() {
  return (
    <MarketingPage
      eyebrow="Case Studies"
      title="Proof, not projections"
      body="Our first case study is being written in production, live, with The Beauty Aesthetic & Lounge. We publish real numbers once they're real — never a projection dressed up as a result."
    >
      <article className="grid gap-8 rounded-[var(--p-radius-xl)] border border-line bg-surface p-6 sm:p-10 lg:grid-cols-[1.3fr_1fr]">
        <div>
          <Badge tone="success" dot>
            In production
          </Badge>
          <h2 className="mt-5 font-display text-3xl font-semibold tracking-[-0.02em] text-fg sm:text-4xl">
            The Beauty Aesthetic &amp; Lounge
          </h2>
          <p className="mt-2 text-fg-muted">Aesthetic clinic, salon and training academy · Gulberg, Lahore</p>
          <p className="mt-6 max-w-xl text-[17px] leading-relaxed text-fg-muted">
            The clinic is TechDeva&apos;s lab: the first business the platform runs for, with Hermes answering clients on
            WhatsApp and every campaign stopping for the owner&apos;s approval. Results will be published here once they are
            measured.
          </p>
        </div>
        <dl className="flex flex-col gap-5 self-center">
          {[
            { Icon: BadgeCheck, k: "Status", v: "Live in production" },
            { Icon: Ruler, k: "What we'll report", v: "Leads, bookings, cost per lead and return on ad spend" },
            { Icon: FileClock, k: "Results", v: "Published once measured" },
          ].map(({ Icon, k, v }) => (
            <div key={k}>
              <dt className="flex items-center gap-3 text-label font-medium uppercase text-fg-subtle">
                <Icon className="h-5 w-5 shrink-0 text-accent-ink" aria-hidden="true" />
                {k}
              </dt>
              <dd className="mt-1 pl-8 text-[15px] text-fg">{v}</dd>
            </div>
          ))}
        </dl>
      </article>
    </MarketingPage>
  );
}
