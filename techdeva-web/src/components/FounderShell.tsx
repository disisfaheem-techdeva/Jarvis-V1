"use client";

import { Activity, Building2, Gauge, Siren, Workflow } from "lucide-react";

import { SidebarLayout, type NavItem } from "@/components/SidebarLayout";

const NAV: NavItem[] = [
  { href: "/founder", label: "Overview", icon: Gauge, primary: true },
  { href: "/founder/tenants", label: "Tenants", icon: Building2, primary: true },
  { href: "/founder/orchestrator", label: "Orchestrator", icon: Workflow, primary: true },
  { href: "/founder/incidents", label: "Incidents", icon: Siren, primary: true },
  { href: "/founder/ai-health", label: "AI Health", icon: Activity },
];

export function FounderShell({ children }: { children: React.ReactNode }) {
  return (
    <SidebarLayout title="Founder console" nav={NAV} root="/founder" tone="founder">
      {children}
    </SidebarLayout>
  );
}
