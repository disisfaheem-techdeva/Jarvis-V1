import type { Metadata } from "next";
import { Basket, Leaf, Sparkle } from "@phosphor-icons/react/dist/ssr";
import { MapEmbed } from "@/components/MapEmbed";
import { MotionImage } from "@/components/MotionImage";
import { PageHeader } from "@/components/PageHeader";
import { Reveal } from "@/components/Reveal";
import { Container } from "@/components/ui";
import { brand, images } from "@/lib/catalog";

export const metadata: Metadata = {
  title: "About us",
  description: "Instant Grill is a Lahore-born brand and Pakistan's first ready-to-grill BBQ box. Our kitchen is at Orange Square, Baghbanpura, near UET.",
  alternates: { canonical: "/about" },
};

const values = [
  { icon: Leaf, title: "Fresh daily", body: "Meat is marinated every day in our own kitchen." },
  { icon: Basket, title: "Everything included", body: "Grill, coal, tong, pita, hummus, chutney and salad. Nothing extra to buy." },
  { icon: Sparkle, title: "No mess", body: "The grill is disposable, so there is nothing to scrub afterwards." },
];

export default function AboutPage() {
  return (
    <>
      <PageHeader title="Made in Lahore, for BBQ anywhere" />
      {/* [TO CONFIRM] owner to review the story wording */}
      <Container className="grid gap-12 py-14 lg:grid-cols-[1fr_1.1fr] lg:gap-16">
        <Reveal className="space-y-5 text-lg leading-relaxed text-ash-300">
          <p>
            Everyone loves a BBQ night. Fewer people love what comes before it: finding a grill, buying coal, marinating the meat
            the night before and cleaning up after.
          </p>
          <p>
            So we put the whole thing in one box. {brand.name} is {brand.positioning.toLowerCase()}: marinated meat, a grill with
            coal, a tong, pita, hummus, chutney and salad, delivered across Lahore. Open it on a rooftop, a lawn or a farmhouse,
            light it, and grill.
          </p>
        </Reveal>
        <div className="rounded-[var(--radius-card)] ring-1 ring-white/[0.06]">
          <MotionImage slot={images.kitchen} radius="20px" sizes="(min-width: 1024px) 50vw, 100vw" className="aspect-[4/3]" />
        </div>
      </Container>

      <Container className="grid gap-4 py-10 md:grid-cols-3">
        {values.map(({ icon: Icon, title, body }, i) => (
          <Reveal key={title} delay={i * 0.06} className={i === 0 ? "md:col-span-1" : ""}>
            <div className="h-full rounded-[var(--radius-card)] bg-coal-900 p-7 ring-1 ring-white/[0.06]">
              <Icon size={28} className="text-ember-400" />
              <h2 className="mt-4 text-xl font-semibold text-ash-50">{title}</h2>
              <p className="mt-2 text-ash-300">{body}</p>
            </div>
          </Reveal>
        ))}
      </Container>

      <Container className="grid items-start gap-10 py-16 lg:grid-cols-[1fr_1.4fr]">
        <Reveal>
          <h2 className="text-3xl font-semibold tracking-[-0.03em] text-ash-50">Our kitchen</h2>
          <p className="mt-4 text-lg text-ash-300">{brand.address}</p>
          <p className="mt-2 text-ash-400">{brand.hoursDisplay}</p>
          <div className="mt-8 rounded-[var(--radius-card)] ring-1 ring-white/[0.06]">
            <MotionImage slot={images.team} radius="20px" sizes="(min-width: 1024px) 35vw, 100vw" className="aspect-[4/3]" />
          </div>
        </Reveal>
        <MapEmbed />
      </Container>
    </>
  );
}
