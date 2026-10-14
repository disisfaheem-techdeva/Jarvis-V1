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
import { faqs } from "@/lib/catalog";
import { faqJsonLd, ldJson, restaurantJsonLd } from "@/lib/jsonld";

function structuredData() {
  return [restaurantJsonLd(), faqJsonLd(faqs.slice(0, 5))];
}

export default function Home() {
  return (
    <>
      <script
        type="application/ld+json"
        // Built from our own catalog constants; `<` is escaped so no string can close the tag.
        dangerouslySetInnerHTML={ldJson(structuredData())}
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
