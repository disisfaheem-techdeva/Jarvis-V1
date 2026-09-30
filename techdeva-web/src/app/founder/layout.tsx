import type { Metadata } from "next";
import { SidebarLayout } from "@/components/SidebarLayout";

export const metadata: Metadata = { robots: { index: false, follow: false } };

const NAV = [
  { href: "/founder", label: "Overview" },
  { href: "/founder/tenants", label: "Tenants" },
  { href: "/founder/orchestrator", label: "Orchestrator" },
  { href: "/founder/incidents", label: "Incidents" },
  { href: "/founder/ai-health", label: "AI Health" },
];

export default function FounderLayout({ children }: { children: React.ReactNode }) {
  return <SidebarLayout title="Founder Console" nav={NAV}>{children}</SidebarLayout>;
}
