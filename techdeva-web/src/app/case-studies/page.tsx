import type { Metadata } from "next";
import { MarketingPage } from "@/components/MarketingPage";

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
    />
  );
}
