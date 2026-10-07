"use client";

import {
  BarChart3,
  CreditCard,
  Hand,
  LayoutDashboard,
  ListChecks,
  Megaphone,
  PlugZap,
  Settings,
  Sprout,
  Users,
} from "lucide-react";
import { useEffect, useState } from "react";

import { SidebarLayout, type NavItem } from "@/components/SidebarLayout";
import { API_BASE } from "@/lib/api";

/** Client portal frame. Shows how many approvals are waiting, read from the same queue the Approvals page uses. */
export function PortalShell({ children }: { children: React.ReactNode }) {
  const waiting = useAwaitingApprovals();

  const nav: NavItem[] = [
    { href: "/portal", label: "Overview", icon: LayoutDashboard, primary: true },
    { href: "/portal/approvals", label: "Approvals", icon: Hand, primary: true, badge: waiting },
    { href: "/portal/leads", label: "Leads", icon: Users, primary: true },
    { href: "/portal/reports", label: "Reports", icon: BarChart3, primary: true },
    { href: "/portal/growth", label: "Growth plan", icon: Sprout, group: "Your AI team" },
    { href: "/portal/campaigns", label: "Campaigns", icon: Megaphone, group: "Your AI team" },
    { href: "/portal/onboarding", label: "Set up business", icon: ListChecks, group: "Business" },
    { href: "/portal/integrations", label: "Connections", icon: PlugZap, group: "Business" },
    { href: "/portal/billing", label: "Billing", icon: CreditCard, group: "Business" },
    { href: "/portal/settings", label: "Settings", icon: Settings, group: "Business" },
  ];

  return (
    <SidebarLayout title="Client portal" nav={nav} root="/portal">
      {children}
    </SidebarLayout>
  );
}

function useAwaitingApprovals(): number | null {
  const [count, setCount] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/portal/deliverables`, { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { awaiting_approval?: number } | null) => {
        if (!cancelled) setCount(typeof d?.awaiting_approval === "number" ? d.awaiting_approval : null);
      })
      .catch(() => {
        if (!cancelled) setCount(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return count;
}
