import type { Metadata } from "next";
import { MarketingPage } from "@/components/MarketingPage";

export const metadata: Metadata = {
  title: "Solutions for clinics, salons, gyms and academies",
  description:
    "TechDeva for aesthetic and skin clinics, dental clinics, salons and spas, gyms and training academies: bookings, " +
    "a WhatsApp AI receptionist, billing, stock and marketing in one place.",
  alternates: { canonical: "/solutions" },
};

export default function SolutionsPage() {
  return (
    <MarketingPage
      eyebrow="Solutions"
      title="Built for service businesses"
      body="Aesthetic clinics, salons, academies, and beyond — the same platform, configured per business. The lab is one real clinic, proven in production, before anything scales."
    >
      <ul className="mt-8 space-y-3 text-sm text-[var(--muted)]">
        <li>• Aesthetic &amp; skin clinics</li>
        <li>• Dental clinics</li>
        <li>• Salons &amp; spas</li>
        <li>• Gyms &amp; fitness studios</li>
        <li>• Academies &amp; training businesses</li>
        <li>• Any appointment-driven service business</li>
      </ul>
    </MarketingPage>
  );
}
