import type { Metadata } from "next";
import { Container } from "@/components/ui";
import { brand, shipping } from "@/lib/catalog";
import { formatPKR } from "@/lib/pricing";

export const metadata: Metadata = { title: "Delivery and returns" };

// EDIT: have the final policy reviewed before launch.
const sections = [
  {
    h: "Delivery",
    p: `We deliver across Pakistan in ${shipping.eta}. Delivery costs ${formatPKR(shipping.fee)} and is free on orders of ${formatPKR(shipping.freeAbove)} or more. We confirm every order by call or WhatsApp before dispatch.`,
  },
  {
    h: "Payment",
    p: "Pay cash to the rider when your order arrives. Please keep the exact amount ready.",
  },
  {
    h: "Damaged or wrong item",
    p: `Send a photo of the item and packaging on WhatsApp within ${shipping.replacementDays} days of delivery and we will replace it at no cost.`,
  },
  {
    h: "Cancellations",
    p: "You can cancel any time before dispatch by messaging us with your order number.",
  },
  {
    h: "Your data",
    p: `We use your name, number and address only to deliver your order and contact you about it. We never sell it. Questions: ${brand.email}.`,
  },
];

export default function PoliciesPage() {
  return (
    <Container className="max-w-3xl py-14 md:py-20">
      <h1 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">Delivery and returns</h1>
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
