import type { Metadata } from "next";
import { BarChart3, Brain, Lock, Megaphone, MessageCircle, Palette, Share2, Target } from "lucide-react";

import { MarketingPage } from "@/components/MarketingPage";
import { Badge } from "@/components/ui/Badge";

export const metadata: Metadata = {
  title: "AI workforce: AI employees for your business",
  description:
    "TechDeva's AI employees: a WhatsApp sales agent, analyst, designer, marketing expert, campaign manager, social media " +
    "expert and reporting, each with clear permissions, and never spending money without your approval.",
  alternates: { canonical: "/ai-workforce" },
};

const AGENTS = [
  { name: "Hermes", role: "WhatsApp, CRM, leads, appointments, customer communication", Icon: MessageCircle, gated: false, first: true },
  { name: "Analyzer", role: "Business intelligence and performance analysis", Icon: Brain, gated: false },
  { name: "Designer", role: "On-brand creative assets", Icon: Palette, gated: false },
  { name: "Marketing Expert", role: "Campaign strategy", Icon: Target, gated: false },
  { name: "Campaign Manager", role: "Ad execution, gated on spend approval", Icon: Megaphone, gated: true },
  { name: "Social Media Expert", role: "Publishing, gated on approval", Icon: Share2, gated: true },
  { name: "Reporting", role: "ROAS, CPL, attribution — null when there's no data, never a fabricated zero", Icon: BarChart3, gated: false },
];

export default function AiWorkforcePage() {
  return (
    <MarketingPage
      eyebrow="AI Workforce"
      title="Employees, not features"
      body="Every AI employee has a mission, permissions, restrictions, KPIs and memory access — and follows the same constitution. None of them can change pricing, memory, or spend money without your approval."
    >
      <ul className="overflow-hidden rounded-[var(--p-radius-xl)] border border-line bg-surface">
        {AGENTS.map(({ name, role, Icon, gated, first }) => (
          <li key={name} className="flex flex-col gap-3 border-b border-line px-5 py-5 last:border-b-0 sm:flex-row sm:items-center sm:gap-5 sm:px-7">
            <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-[var(--p-radius-md)] bg-accent-tint text-accent-ink">
              <Icon className="h-5 w-5" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="flex flex-wrap items-center gap-2 text-base font-semibold text-fg">
                {name}
                {first ? <Badge tone="success" dot>Employee #1 · live</Badge> : null}
              </h2>
              <p className="mt-1 text-[15px] text-fg-muted">{role}</p>
            </div>
            {gated ? (
              <Badge tone="warning" className="self-start sm:self-center">
                <Lock className="h-3 w-3" aria-hidden="true" /> Needs your approval
              </Badge>
            ) : null}
          </li>
        ))}
      </ul>
    </MarketingPage>
  );
}
