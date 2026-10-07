import type { Metadata } from "next";

import { FounderShell } from "@/components/FounderShell";

export const metadata: Metadata = {
  title: { default: "Founder console", template: "%s · TechDeva founder" },
};

export default function FounderLayout({ children }: { children: React.ReactNode }) {
  return <FounderShell>{children}</FounderShell>;
}
