import { CheckCircle } from "@phosphor-icons/react/dist/ssr";
import { BOX_INCLUDES, images } from "@/lib/catalog";
import { MotionImage } from "../MotionImage";
import { Reveal } from "../Reveal";
import { Container } from "../ui";

export function InEveryBox() {
  return (
    <section className="border-t border-white/[0.06] bg-coal-900 py-24 md:py-32">
      <Container className="grid items-center gap-12 lg:grid-cols-[1.3fr_1fr] lg:gap-16">
        <div className="rounded-[var(--radius-card)] ring-1 ring-white/[0.06]">
          <MotionImage slot={images.everything} radius="20px" parallax={5} sizes="(min-width: 1024px) 60vw, 100vw" className="aspect-[16/10]" />
        </div>
        <Reveal>
          <h2 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">In every box</h2>
          <ul className="mt-8 space-y-4">
            {BOX_INCLUDES.map((i) => (
              <li key={i} className="flex items-center gap-3 text-lg text-ash-100">
                <CheckCircle size={24} weight="fill" className="shrink-0 text-ember-400" /> {i}
              </li>
            ))}
          </ul>
        </Reveal>
      </Container>
    </section>
  );
}
