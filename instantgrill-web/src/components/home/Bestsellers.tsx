import { BESTSELLERS, findProduct } from "@/lib/catalog";
import { ProductCard } from "../menu/ProductCard";
import { Reveal } from "../Reveal";
import { ButtonLink, Container } from "../ui";

export function Bestsellers() {
  const items = BESTSELLERS.map(findProduct).filter((p) => p !== undefined);
  return (
    <section className="py-24 md:py-32">
      <Container>
        <Reveal>
          <h2 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">Bestsellers</h2>
        </Reveal>
        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {items.map((p) => (
            <ProductCard key={p.slug} product={p} />
          ))}
        </div>
        <ButtonLink href="/menu" variant="ghost" size="lg" className="mt-10">
          See the full menu
        </ButtonLink>
      </Container>
    </section>
  );
}
