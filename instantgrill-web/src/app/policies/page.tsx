import type { Metadata } from "next";
import { Container } from "@/components/ui";
import { brand, delivery } from "@/lib/catalog";
import { formatPKR } from "@/lib/pricing";

export const metadata: Metadata = {
  title: "Delivery and orders",
  description: "How Instant Grill delivery works in Lahore: delivery times, payment, changes and safe grilling.",
  alternates: { canonical: "/policies" },
};

const sections = [
  {
    h: "Delivery",
    p: `We deliver across ${delivery.city} with our own riders. Orders are delivered within 6 hours. Orders placed after 5:30 PM, or between 12 AM and 10 AM, are delivered the next morning between 10 AM and 12 PM. You can also pick a later time, up to 7 days ahead. ${
      delivery.fee === null ? "The delivery fee is confirmed when we call or message you." : `Delivery costs ${formatPKR(delivery.fee)}.`
    }`,
  },
  { h: "Opening hours", p: "We take and confirm orders daily from 12 PM to 12 AM. Orders placed online outside these hours are confirmed when we open." },
  { h: "Payment", p: "Cash on delivery. Please keep the amount ready for the rider. JazzCash and Easypaisa are coming soon." },
  { h: "Changes and cancellations", p: `Message us on WhatsApp at ${brand.phoneDisplay} with your order number before the order is out for delivery.` },
  { h: "Storage", p: "If you are not grilling right away, keep the box in the fridge and grill it the same day." },
  { h: "Safety", p: "Use the grill outdoors or in the open air only, never indoors. Keep children away. Let the coals cool completely before disposal." },
  { h: "Your data", p: `We use your name, number and address only to deliver your order and contact you about it. We never sell it. Questions: ${brand.email}.` },
];

export default function PoliciesPage() {
  return (
    <Container className="max-w-3xl py-14 md:py-20">
      <h1 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">Delivery and orders</h1>
      <div className="mt-12 space-y-10">
        {sections.map((s) => (
          <section key={s.h}>
            <h2 className="text-xl font-semibold text-ash-50">{s.h}</h2>
            <p className="mt-3 max-w-[65ch] leading-relaxed text-ash-300">{s.p}</p>
          </section>
        ))}
      </div>
    </Container>
  );
}
