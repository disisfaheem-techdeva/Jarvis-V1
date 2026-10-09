import type { Metadata } from "next";

import { PortalShell } from "@/components/PortalShell";

export const metadata: Metadata = {
  title: { default: "Client portal", template: "%s · TechDeva portal" },
  robots: { index: false, follow: false },
};

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  return <PortalShell>{children}</PortalShell>;
}
