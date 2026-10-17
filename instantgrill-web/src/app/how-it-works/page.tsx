import type { Metadata } from "next";
import { Fire, Snowflake, Warning } from "@phosphor-icons/react/dist/ssr";
import { ImageSlot } from "@/components/ImageSlot";
import { MotionImage } from "@/components/MotionImage";
import { PageHeader } from "@/components/PageHeader";
import { Reveal } from "@/components/Reveal";
import { ButtonLink, Container } from "@/components/ui";
import { grillGuide, images, media, steps } from "@/lib/catalog";

export const metadata: Metadata = {
  title: "How it works: order, light, grill",
  description: "How the Instant Grill BBQ box works: order online or on WhatsApp, we deliver it in Lahore, light the coal for about 10 minutes and grill. Outdoor use only.",
  alternates: { canonical: "/how-it-works" },
};

export default function HowItWorksPage() {
  const { unboxing } = media;
  return (
    <>
      <PageHeader title="BBQ ghar pe, in four steps" intro="Everything you need comes in the box, down to the coal and the tong." />

      <Container className="space-y-20 py-16 md:space-y-28">
        {steps.map((s, i) => (
          <section key={s.title} className={`grid items-center gap-8 md:grid-cols-2 md:gap-16 ${i % 2 ? "md:[&>*:first-child]:order-2" : ""}`}>
            <div className="rounded-[var(--radius-card)] ring-1 ring-white/[0.06]">
              <MotionImage slot={images[s.image]} radius="20px" parallax={5} sizes="(min-width: 768px) 50vw, 100vw" className="aspect-[4/3]" />
            </div>
            <Reveal>
              <span className="font-mono text-sm text-ember-400">{i + 1}</span>
              <h2 className="mt-2 text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">{s.title}</h2>
              <p className="mt-4 max-w-[44ch] text-lg leading-relaxed text-ash-300">{s.body}</p>
            </Reveal>
          </section>
        ))}
      </Container>

      <Container className="py-8">
        <div className="overflow-hidden rounded-[var(--radius-card)] ring-1 ring-white/[0.06]">
          {unboxing.mp4 ? (
            <video className="aspect-video w-full bg-coal-900 object-cover" controls playsInline muted preload="none" poster={unboxing.poster.src ?? undefined}>
              {unboxing.webm && <source src={unboxing.webm} type="video/webm" />}
              <source src={unboxing.mp4} type="video/mp4" />
            </video>
          ) : (
            <ImageSlot slot={{ ...unboxing.poster, brief: "VIDEO: the unboxing clip goes here (MP4 + WebM under 2 MB)." }} className="aspect-video" />
          )}
        </div>
      </Container>

      <Container className="grid gap-4 py-16 md:grid-cols-[1.2fr_1fr]">
        <section className="rounded-[var(--radius-card)] bg-coal-900 p-8 ring-1 ring-white/[0.06]">
          <h2 className="flex items-center gap-2 text-2xl font-semibold text-ash-50">
            <Fire size={24} weight="fill" className="text-ember-400" /> Grilling guide
          </h2>
          <ol className="mt-6 space-y-4">
            {grillGuide.map((g, i) => (
              <li key={g} className="flex gap-4 text-ash-100">
                <span className="font-mono text-ember-400">{i + 1}</span> {g}
              </li>
            ))}
          </ol>
        </section>
        <div className="grid gap-4">
          <section className="rounded-[var(--radius-card)] bg-gradient-to-br from-danger/15 to-coal-900 p-8 ring-1 ring-danger/30">
            <h2 className="flex items-center gap-2 text-xl font-semibold text-ash-50">
              <Warning size={22} className="text-danger" /> Safety
            </h2>
            <ul className="mt-4 space-y-2 text-ash-100">
              <li>Outdoor or open-air use only. Never indoors or in a closed room.</li>
              <li>Keep children and pets away from the hot grill.</li>
              <li>Let the coals cool completely before you throw it away.</li>
            </ul>
          </section>
          <section className="rounded-[var(--radius-card)] bg-coal-900 p-8 ring-1 ring-white/[0.06]">
            <h2 className="flex items-center gap-2 text-xl font-semibold text-ash-50">
              <Snowflake size={22} className="text-ember-400" /> Storage
            </h2>
            <p className="mt-4 text-ash-100">Not grilling right away? Keep the box in the fridge and grill it the same day.</p>
          </section>
        </div>
      </Container>

      <Container className="pb-24 text-center">
        <ButtonLink href="/menu" size="lg">
          Build your box
        </ButtonLink>
      </Container>
    </>
  );
}
