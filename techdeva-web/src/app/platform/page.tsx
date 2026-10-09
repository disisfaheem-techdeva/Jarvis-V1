import type { Metadata } from "next";
import { Database, Hand, Network, ScrollText } from "lucide-react";

import { MarketingPage } from "@/components/MarketingPage";
import { GlowCard } from "@/components/ui/motion";

export const metadata: Metadata = { title: "Platform" };

const PILLARS = [
  {
    Icon: Network,
    title: "Master Orchestrator",
    body: "A rulebook and task router, not a chatbot. It decides which AI employee does what, and when.",
  },
  {
    Icon: ScrollText,
    title: "Agent Contract",
    body: "Every AI employee reads config and events only. The orchestrator is the only thing with write access.",
  },
  {
    Icon: Hand,
    title: "Human in the loop, in code",
    body: "Money, publishing and pricing always park for approval. It's enforced in the platform, not just in policy.",
  },
  {
    Icon: Database,
    title: "Isolated by design",
    body: "Every business is isolated at the database layer with row-level security. The app never connects as a superuser.",
  },
];

export default function PlatformPage() {
  return (
    <MarketingPage
      eyebrow="Platform"
      title="One engine, every industry"
      body="Business rules — services, prices, persona, hours, departments — live in configuration, not code. Onboarding a new industry is a config row, never a rebuild."
    >
      <ul className="grid gap-4 md:grid-cols-2">
        {PILLARS.map(({ Icon, title, body }) => (
          <GlowCard as="li" key={title} className="rounded-[var(--card-radius)] border border-line bg-surface p-7">
            <Icon className="h-5 w-5 text-accent-ink" aria-hidden="true" />
            <h2 className="mt-5 text-lg font-semibold text-fg">{title}</h2>
            <p className="mt-2 text-[15px] leading-relaxed text-fg-muted">{body}</p>
          </GlowCard>
        ))}
      </ul>
    </MarketingPage>
  );
}
