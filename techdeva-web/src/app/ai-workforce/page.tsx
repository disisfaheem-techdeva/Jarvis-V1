import type { Metadata } from "next";
import { MarketingPage } from "@/components/MarketingPage";

export const metadata: Metadata = {
  title: "AI workforce: AI employees for your business",
  description:
    "TechDeva's AI employees: a WhatsApp sales agent, analyst, designer, marketing expert, campaign manager, social media " +
    "expert and reporting, each with clear permissions, and never spending money without your approval.",
  alternates: { canonical: "/ai-workforce" },
};


const AGENTS = [
  ["Hermes", "WhatsApp, CRM, leads, appointments, customer communication"],
  ["Analyzer", "Business intelligence and performance analysis"],
  ["Designer", "On-brand creative assets"],
  ["Marketing Expert", "Campaign strategy"],
  ["Campaign Manager", "Ad execution, gated on spend approval"],
  ["Social Media Expert", "Publishing, gated on approval"],
  ["Reporting", "ROAS, CPL, attribution — null when there's no data, never a fabricated zero"],
];

export default function AiWorkforcePage() {
  return (
    <MarketingPage
      eyebrow="AI Workforce"
      title="Employees, not features"
      body="Every AI employee has a mission, permissions, restrictions, KPIs and memory access — and follows the same constitution. None of them can change pricing, memory, or spend money without your approval."
    >
      <div className="mt-8 divide-y divide-white/10">
        {AGENTS.map(([name, desc]) => (
          <div key={name} className="flex flex-col gap-1 py-4 sm:flex-row sm:items-baseline sm:justify-between">
            <span className="font-medium text-white">{name}</span>
            <span className="text-sm text-[var(--muted)]">{desc}</span>
          </div>
        ))}
      </div>
    </MarketingPage>
  );
}
