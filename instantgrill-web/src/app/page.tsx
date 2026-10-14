import { Hero } from "@/components/home/Hero";
import { TrustStrip } from "@/components/home/TrustStrip";
import { WhyBento } from "@/components/home/WhyBento";
import { HowItWorks } from "@/components/home/HowItWorks";
import { Compare } from "@/components/home/Compare";
import { BuyBox } from "@/components/home/BuyBox";
import { Reviews } from "@/components/home/Reviews";
import { Faq } from "@/components/home/Faq";
import { FinalCta } from "@/components/home/FinalCta";
import { MobileBuyBar } from "@/components/home/MobileBuyBar";
import { brand, faqs, product } from "@/lib/catalog";

function structuredData() {
  const url = `https://${brand.domain}`;
  return [
    {
      "@context": "https://schema.org",
      "@type": "Product",
      name: product.name,
      description: product.summary,
      brand: { "@type": "Brand", name: brand.name },
      url,
      offers: product.bundles.map((b) => ({
        "@type": "Offer",
        name: b.name,
        price: b.price,
        priceCurrency: brand.currency,
        availability: "https://schema.org/InStock",
        url: `${url}/#shop`,
      })),
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
      <HowItWorks />
      <Compare />
      <BuyBox />
      <Reviews />
      <Faq />
      <FinalCta />
      <MobileBuyBar />
    </>
  );
}
