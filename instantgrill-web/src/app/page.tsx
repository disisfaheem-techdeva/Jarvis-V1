import { Hero } from "@/components/home/Hero";
import { TrustStrip } from "@/components/home/TrustStrip";
import { WhyBento } from "@/components/home/WhyBento";
import { Bestsellers } from "@/components/home/Bestsellers";
import { HowItWorks } from "@/components/home/HowItWorks";
import { InEveryBox } from "@/components/home/InEveryBox";
import { DealsStrip } from "@/components/home/DealsStrip";
import { Reviews } from "@/components/home/Reviews";
import { Faq } from "@/components/home/Faq";
import { FinalCta } from "@/components/home/FinalCta";
import { MobileBuyBar } from "@/components/home/MobileBuyBar";
import { brand, faqs } from "@/lib/catalog";

function structuredData() {
  const url = `https://${brand.domain}`;
  return [
    {
      "@context": "https://schema.org",
      "@type": "Restaurant",
      name: brand.name,
      description: brand.positioning,
      url,
      telephone: "+" + brand.whatsapp,
      email: brand.email,
      servesCuisine: "BBQ",
      priceRange: "Rs 3000-12500",
      address: {
        "@type": "PostalAddress",
        streetAddress: "Orange Square, Baghbanpura, near UET",
        addressLocality: "Lahore",
        addressCountry: "PK",
      },
      openingHours: "Mo-Su 12:00-24:00",
      sameAs: [brand.instagram, brand.tiktok],
    },
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: faqs.map((f) => ({
        "@type": "Question",
        name: f.q,
        acceptedAnswer: { "@type": "Answer", text: f.a },
      })),
    },
  ];
}

export default function Home() {
  return (
    <>
      <script
        type="application/ld+json"
        // Built from our own catalog constants; `<` is escaped so no string can close the tag.
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData()).replace(/</g, "\\u003c") }}
      />
      <Hero />
      <TrustStrip />
      <WhyBento />
      <Bestsellers />
      <HowItWorks />
      <InEveryBox />
      <DealsStrip />
      <Reviews />
      <Faq />
      <FinalCta />
      <MobileBuyBar />
    </>
  );
}
