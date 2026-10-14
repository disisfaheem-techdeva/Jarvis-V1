import type { Metadata } from "next";
import { LegalPage } from "@/components/LegalPage";
import { brand } from "@/lib/catalog";

export const metadata: Metadata = {
  title: "Terms of service",
  description: "The terms for ordering Instant Grill BBQ boxes in Lahore: orders, delivery, payment, cancellations and safe use.",
  alternates: { canonical: "/terms" },
};

// [TO CONFIRM] have this reviewed before launch.
export default function TermsPage() {
  return (
    <LegalPage
      title="Terms of service"
      updated="14 October 2026"
      sections={[
        { h: "Orders", p: "An order is confirmed when our team confirms it with you by call or WhatsApp. Prices on the site are in Pakistani rupees. We may decline an order we cannot deliver, for example outside our delivery areas." },
        { h: "Delivery", p: "We deliver in Lahore with our own riders, in the time slot you pick at checkout. Traffic or weather can cause delays; we will keep you updated on WhatsApp. Delivery fees are shown at checkout or confirmed with you before dispatch." },
        { h: "Payment", p: "Cash on delivery. Please pay the rider the confirmed amount." },
        { h: "Changes and cancellations", p: `Message us at ${brand.phoneDisplay} with your order number before your order is out for delivery. Once the food is prepared for your slot we may not be able to cancel it.` },
        { h: "Food and allergies", p: "Our marinades may contain dairy, nuts, sesame, gluten or other allergens. Ask us before ordering if you have an allergy. Cook meat through before eating. Refrigerate if not grilling right away and grill on the same day." },
        { h: "Safe use", p: "The grill is for outdoor or open-air use only, never indoors or in a closed space. Keep children and pets away. Let the coals cool completely before disposal. You are responsible for using the grill safely." },
        { h: "Contact", p: `${brand.name}, ${brand.address}. ${brand.email}.` },
      ]}
    />
  );
}
