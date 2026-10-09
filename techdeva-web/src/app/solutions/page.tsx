import type { Metadata } from "next";
import { CalendarClock, GraduationCap, Scissors, Stethoscope } from "lucide-react";

import { MarketingPage } from "@/components/MarketingPage";
import { GlowCard } from "@/components/ui/motion";

export const metadata: Metadata = { title: "Solutions" };

const SEGMENTS = [
  {
    Icon: Stethoscope,
    title: "Clinics & aesthetic lounges",
    body: "Treatment enquiries answered on WhatsApp from your own price list, consultations booked inside your hours.",
  },
  {
    Icon: Scissors,
    title: "Salons",
    body: "Bookings, follow-ups and offers handled for you, with every promotion approved by you before it goes out.",
  },
  {
    Icon: GraduationCap,
    title: "Academies & training businesses",
    body: "Course enquiries qualified and followed up, so the people ready to enrol reach you first.",
  },
  {
    Icon: CalendarClock,
    title: "Any appointment-driven business",
    body: "If your business runs on enquiries and bookings, the same engine is configured around how you work.",
  },
];

export default function SolutionsPage() {
  return (
    <MarketingPage
      eyebrow="Solutions"
      title="Built for service businesses"
      body="Aesthetic clinics, salons, academies, and beyond — the same platform, configured per business. The lab is one real clinic, proven in production, before anything scales."
    >
      <ul className="grid gap-4 md:grid-cols-2">
        {SEGMENTS.map(({ Icon, title, body }) => (
          <GlowCard as="li" key={title} className="rounded-[var(--card-radius)] border border-line bg-surface p-7">
            <span className="inline-flex h-11 w-11 items-center justify-center rounded-[var(--p-radius-md)] bg-accent-tint text-accent-ink">
              <Icon className="h-5 w-5" aria-hidden="true" />
            </span>
            <h2 className="mt-5 text-lg font-semibold text-fg">{title}</h2>
            <p className="mt-2 text-[15px] leading-relaxed text-fg-muted">{body}</p>
          </GlowCard>
        ))}
      </ul>
    </MarketingPage>
  );
}
