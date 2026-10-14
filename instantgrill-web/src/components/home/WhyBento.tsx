import { images, reasons } from "@/lib/catalog";
import { MotionImage } from "../MotionImage";
import { Reveal } from "../Reveal";
import { Container } from "../ui";

/**
 * Five reasons, five cells (no empty tiles). Desktop grid, 6 columns:
 *   row 1   [ fast ............ ][ portable ]
 *   row 2   [ clean ][ build   ][ portable ]
 *   row 3   [ flavour, full-width cinematic ]
 * Mobile: single column in reading order.
 */
const layout: Record<string, { cell: string; media: string }> = {
  fast: { cell: "md:col-span-4", media: "aspect-[16/10] md:aspect-auto md:min-h-[340px]" },
  portable: { cell: "md:col-span-2 md:row-span-2", media: "aspect-[4/5] md:aspect-auto md:h-full" },
  clean: { cell: "md:col-span-2", media: "aspect-square md:aspect-auto md:min-h-[300px]" },
  build: { cell: "md:col-span-2", media: "aspect-square md:aspect-auto md:min-h-[300px]" },
  flavour: { cell: "md:col-span-6", media: "aspect-[4/3] md:aspect-[21/8]" },
};

const order = ["fast", "portable", "clean", "build", "flavour"];

export function WhyBento() {
  const byKey = Object.fromEntries(reasons.map((r) => [r.key, r]));
  return (
    <section id="why" className="scroll-mt-16 py-24 md:py-32">
      <Container>
        <Reveal>
          <h2 className="max-w-2xl text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">
            Why Instant Grill?
          </h2>
          <p className="mt-4 max-w-[52ch] text-lg leading-relaxed text-ash-300">
            Everything annoying about a charcoal BBQ, fixed. The flavour stays.
          </p>
        </Reveal>

        <div className="mt-14 grid grid-cols-1 gap-4 md:grid-cols-6 md:grid-rows-[auto_auto_auto]">
          {order.map((key, i) => {
            const r = byKey[key];
            const l = layout[key];
            const wide = key === "flavour" || key === "fast";
            return (
              <Reveal key={key} delay={i * 0.06} className={l.cell}>
                <article className="group relative h-full overflow-hidden rounded-[var(--radius-card)] bg-coal-850 ring-1 ring-white/[0.06]">
                  <MotionImage
                    slot={images[r.image]}
                    delay={i * 0.06}
                    parallax={wide ? 8 : 6}
                    sizes={wide ? "(min-width: 768px) 66vw, 100vw" : "(min-width: 768px) 33vw, 100vw"}
                    className={`${l.media} w-full transition-transform duration-700 ease-[var(--ease-out-soft)] group-hover:scale-[1.03]`}
                  />
                  <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-coal-950 via-coal-950/55 to-transparent" />
                  <div className="absolute inset-x-0 bottom-0 p-6 md:p-8">
                    <h3
                      className={`font-semibold tracking-[-0.02em] text-ash-50 ${
                        wide ? "max-w-lg text-2xl md:text-3xl" : "text-xl md:text-2xl"
                      }`}
                    >
                      {r.title}
                    </h3>
                    <p className="mt-2 max-w-md text-[15px] leading-relaxed text-ash-300">{r.body}</p>
                  </div>
                </article>
              </Reveal>
            );
          })}
        </div>
      </Container>
    </section>
  );
}
