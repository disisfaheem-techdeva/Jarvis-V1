import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { BoxBuilder } from "@/components/menu/BoxBuilder";
import { ProductCard } from "@/components/menu/ProductCard";
import { MotionImage } from "@/components/MotionImage";
import { Container } from "@/components/ui";
import { brand, findProduct, isBox, products } from "@/lib/catalog";

export function generateStaticParams() {
  return products.map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const p = findProduct((await params).slug);
  if (!p) return {};
  return {
    title: `${p.name} BBQ box`,
    description: `${p.name}: ${p.description} Rs ${p.price.toLocaleString("en-PK")}. Delivered across Lahore.`,
    alternates: { canonical: `/menu/${p.slug}` },
  };
}

export default async function ProductPage({ params }: { params: Promise<{ slug: string }> }) {
  const p = findProduct((await params).slug);
  if (!p) notFound();

  const related = products
    .filter((o) => o.slug !== p.slug && isBox(o) && (o.category === p.category || !isBox(p)))
    .slice(0, 3);
  const fill = products.filter((o) => isBox(o) && o.slug !== p.slug && !related.includes(o));
  while (related.length < 3 && fill.length) related.push(fill.shift()!);

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "Product",
    name: p.name,
    description: p.description,
    brand: { "@type": "Brand", name: brand.name },
    url: `https://${brand.domain}/menu/${p.slug}`,
    offers: {
      "@type": "Offer",
      price: p.price,
      priceCurrency: brand.currency,
      availability: p.soldOut ? "https://schema.org/OutOfStock" : "https://schema.org/InStock",
      areaServed: "Lahore",
    },
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, "\\u003c") }}
      />
      <Container className="grid gap-10 py-12 md:py-16 lg:grid-cols-[1.1fr_1fr] lg:gap-16">
        <div className="lg:sticky lg:top-24 lg:self-start">
          <div className="rounded-[var(--radius-card)] ring-1 ring-white/[0.06]">
            <MotionImage slot={p.image} priority radius="20px" parallax={0} sizes="(min-width: 1024px) 50vw, 100vw" className="aspect-square" />
          </div>
        </div>
        <BoxBuilder product={p} />
      </Container>

      <Container className="border-t border-white/[0.06] py-16">
        <h2 className="text-3xl font-semibold tracking-[-0.03em] text-ash-50">You may also like</h2>
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {related.map((r) => (
            <ProductCard key={r.slug} product={r} />
          ))}
        </div>
      </Container>
    </>
  );
}
