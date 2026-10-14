import { brand, faqs } from "./catalog";

export function restaurantJsonLd() {
  return {
    "@context": "https://schema.org",
    "@type": "Restaurant",
    name: brand.name,
    description: brand.positioning,
    url: `https://${brand.domain}`,
    telephone: "+" + brand.whatsapp,
    email: brand.email,
    servesCuisine: ["BBQ", "Pakistani"],
    priceRange: "Rs 3000-12500",
    address: {
      "@type": "PostalAddress",
      streetAddress: "Orange Square, Baghbanpura, near UET",
      addressLocality: "Lahore",
      addressRegion: "Punjab",
      addressCountry: "PK",
    },
    // geo: [TO CONFIRM] exact kitchen coordinates
    areaServed: { "@type": "City", name: "Lahore" },
    openingHours: "Mo-Su 12:00-24:00",
    sameAs: [brand.instagram, brand.tiktok],
  };
}

export function faqJsonLd(items = faqs) {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: items.map((f) => ({ "@type": "Question", name: f.q, acceptedAnswer: { "@type": "Answer", text: f.a } })),
  };
}

/** Safe to drop into a <script>: `<` is escaped so no string can close the tag. */
export const ldJson = (data: unknown) => ({ __html: JSON.stringify(data).replace(/</g, "\\u003c") });
