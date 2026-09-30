import type { Metadata } from "next";
import { JsonLd } from "@/components/JsonLd";
import { MarketingPage } from "@/components/MarketingPage";
import { TRIAL_DAYS } from "@/lib/links";
import { SITE_URL } from "@/lib/site";
import { PACKAGES } from "./packages";
import { PricingCards } from "./PricingCards";

export const metadata: Metadata = {
  title: "Pricing: Starter, Growth and Pro packages",
  description:
    "TechDeva packages from PKR 19,999 a month (USD 99 abroad): a WhatsApp AI agent, bookings, invoices, stock, team and HR, " +
    "social posting, ad results, Jarvis and a growth advisor with ad autopilot. Replaces tools that cost up to PKR 170,000 a month. " +
    "7-day free trial, no setup fee.",
  alternates: { canonical: "/pricing" },
  openGraph: { url: "/pricing", title: "TechDeva pricing: Starter, Growth and Pro" },
};

const FAQ = [
  ["How much does TechDeva cost?",
    "Starter is PKR 19,999 a month, Growth PKR 49,999 and Pro PKR 64,999. Outside Pakistan the same packages are USD 99, 199 and 299 a month."],
  ["Is there a free trial?",
    `Yes. Every package starts with a ${TRIAL_DAYS}-day free trial with no card and no setup fee. Onboarding and connecting your WhatsApp are included.`],
  ["What does TechDeva replace?",
    "A WhatsApp chatbot tool (like WATI or respond.io), booking software (like Vagaro or Fresha), accounting and inventory apps (like Zoho Books and Zoho Inventory), a social media scheduler, an ad reporting dashboard, a team and HR app, an AI assistant, and an ad automation tool."],
  ["What is an AI chat?",
    "One customer who writes to your business on WhatsApp in a month, however many messages they send. If you go over, your agent keeps answering and we talk to you about the next package."],
  ["Which businesses is it for?",
    "Aesthetic and skin clinics, dental clinics, salons and spas, gyms, and training academies: any business that books appointments and gets enquiries on WhatsApp."],
  ["What happens after the trial?",
    "Your dashboard locks until the first payment. You can pay by bank transfer, JazzCash or Easypaisa in Pakistan."],
] as const;

const LD = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Product",
      name: "TechDeva AIBOS",
      url: `${SITE_URL}/pricing`,
      description: "Business dashboard with a WhatsApp AI agent for clinics, salons, gyms and academies.",
      brand: { "@type": "Brand", name: "TechDeva" },
      offers: PACKAGES.flatMap((p) => [
        { "@type": "Offer", name: p.name, price: String(p.pkr), priceCurrency: "PKR", url: `${SITE_URL}/pricing` },
        { "@type": "Offer", name: `${p.name} (outside Pakistan)`, price: String(p.usd), priceCurrency: "USD", url: `${SITE_URL}/pricing` },
      ]),
    },
    {
      "@type": "FAQPage",
      mainEntity: FAQ.map(([q, a]) => ({ "@type": "Question", name: q, acceptedAnswer: { "@type": "Answer", text: a } })),
    },
  ],
};

export default function PricingPage() {
  return (
    <MarketingPage
      wide
      eyebrow="Pricing"
      title="Pick your package. Drop your other tools."
      body={`Each package replaces tools a clinic, salon or academy pays for separately. The bigger the package, the more of your marketing runs on its own. Every package starts with a ${TRIAL_DAYS}-day free trial and has no setup fee.`}
    >
      <JsonLd data={LD} />
      <PricingCards />
      <p className="mt-6 text-sm text-[var(--muted)]">
        In Pakistan: Starter PKR 19,999, Growth PKR 49,999, Pro PKR 64,999 a month. Outside Pakistan: USD 99, 199 and 299 a month.
        Tool prices are 2026 monthly list prices of each vendor, converted at PKR 277 per US dollar; WhatsApp message fees and ad
        spend are extra with those tools too.
      </p>

      <section className="mt-14 max-w-3xl">
        <h2 className="text-2xl font-semibold text-white">Questions about pricing</h2>
        <dl className="mt-6 space-y-5">
          {FAQ.map(([q, a]) => (
            <div key={q}>
              <dt className="font-medium text-white">{q}</dt>
              <dd className="mt-1 text-sm text-[var(--muted)]">{a}</dd>
            </div>
          ))}
        </dl>
      </section>
    </MarketingPage>
  );
}
