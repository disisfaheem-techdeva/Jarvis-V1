import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Book a demo",
  description: "See TechDeva on your own business: a WhatsApp AI agent, bookings, bills and marketing in one dashboard. Tell us about your business and we will call or message you.",
  alternates: { canonical: "/demo" },
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
