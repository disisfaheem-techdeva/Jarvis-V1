import type { Metadata } from "next";
import { LegalPage } from "@/components/LegalPage";
import { brand } from "@/lib/catalog";

export const metadata: Metadata = {
  title: "Privacy policy",
  description: "What Instant Grill collects when you order, why, who sees it and how long we keep it.",
  alternates: { canonical: "/privacy" },
};

// [TO CONFIRM] have this reviewed before launch.
export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy policy"
      updated="14 October 2026"
      sections={[
        { h: "What we collect", p: "When you order: your name, mobile number, delivery area, address, landmark, notes and what you ordered. When you send an enquiry or message: what you type in the form. We also record which ad or link brought you to the site, if any." },
        { h: "Why we use it", p: "To prepare and deliver your order, to call or WhatsApp you about it, and to answer your enquiries. We use anonymous visit statistics to improve the site and our ads." },
        { h: "Who sees it", p: "Our team and the rider delivering your order. Our hosting, database and email providers process it for us. If you accept analytics, Meta and Google receive visit and purchase events; your phone number is only ever sent to Meta in hashed (unreadable) form. We never sell your data." },
        { h: "How long we keep it", p: "Order records are kept for accounting for as long as the law requires. You can ask us to delete your details at any time if no order is open." },
        { h: "Your choices", p: `To see, correct or delete your details, message us on WhatsApp at ${brand.phoneDisplay} or email ${brand.email}.` },
        { h: "Cookies", p: "We keep your cart in your browser. Admin pages use a login cookie. Analytics tools (Meta Pixel, Google Analytics) set their own cookies when they are enabled." },
      ]}
    />
  );
}
