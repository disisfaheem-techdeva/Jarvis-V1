import type { Metadata } from "next";
import { CalendarClock, Dumbbell, GraduationCap, Scissors, Smile, Stethoscope } from "lucide-react";

import { MarketingPage } from "@/components/MarketingPage";
import { GlowCard } from "@/components/ui/motion";

export const metadata: Metadata = {
  title: "Solutions for clinics, salons, gyms and academies",
  description:
    "TechDeva for aesthetic and skin clinics, dental clinics, salons and spas, gyms and training academies: bookings, " +
    "a WhatsApp AI receptionist, billing, stock and marketing in one place.",
  alternates: { canonical: "/solutions" },
};

const SEGMENTS = [
  { label: "Aesthetic & skin clinics", Icon: Stethoscope },
  { label: "Dental clinics", Icon: Smile },
  { label: "Salons & spas", Icon: Scissors },
  { label: "Gyms & fitness studios", Icon: Dumbbell },
  { label: "Academies & training businesses", Icon: GraduationCap },
  { label: "Any appointment-driven service business", Icon: CalendarClock },
];

export default function SolutionsPage() {
  return (
    <MarketingPage
      eyebrow="Solutions"
      title="Built for service businesses"
      body="Aesthetic clinics, salons, academies, and beyond — the same platform, configured per business. The lab is one real clinic, proven in production, before anything scales."
    >
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {SEGMENTS.map(({ label, Icon }) => (
          <GlowCard as="li" key={label} className="flex items-center gap-4 rounded-[var(--card-radius)] border border-line bg-surface p-6">
            <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-[var(--p-radius-md)] bg-accent-tint text-accent-ink">
              <Icon className="h-5 w-5" aria-hidden="true" />
            </span>
            <span className="text-base font-semibold text-fg">{label}</span>
          </GlowCard>
        ))}
      </ul>
    </MarketingPage>
  );
}
