import type { Metadata } from "next";

import { FounderShell } from "@/components/FounderShell";

export const metadata: Metadata = {
  title: { default: "Founder console", template: "%s · TechDeva founder" },
  robots: { index: false, follow: false },
};

export default function FounderLayout({ children }: { children: React.ReactNode }) {
  return <FounderShell>{children}</FounderShell>;
}
