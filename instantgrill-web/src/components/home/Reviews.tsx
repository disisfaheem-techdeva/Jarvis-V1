import { Star } from "@phosphor-icons/react/dist/ssr";
import { reviews } from "@/lib/catalog";
import { Container } from "../ui";

/** Renders nothing until real reviews are added to lib/catalog.ts. */
export function Reviews() {
  if (reviews.length === 0) return null;
  const loop = [...reviews, ...reviews];
  return (
    <section aria-label="Customer reviews" className="overflow-hidden border-t border-white/[0.06] py-24">
      <Container>
        <h2 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">From our customers</h2>
      </Container>
      <div className="group mt-12 flex w-max gap-4 motion-safe:animate-[marquee_60s_linear_infinite] hover:[animation-play-state:paused]">
        {loop.map((r, i) => (
          <figure
            key={i}
            aria-hidden={i >= reviews.length}
            className="w-[340px] shrink-0 rounded-[var(--radius-card)] bg-coal-850 p-6 ring-1 ring-white/[0.06]"
          >
            <div className="flex gap-0.5 text-ember-400" aria-label={`${r.rating} out of 5`}>
              {Array.from({ length: r.rating }, (_, s) => (
                <Star key={s} size={16} weight="fill" />
              ))}
            </div>
            <blockquote className="mt-4 line-clamp-3 leading-relaxed text-ash-100">“{r.text}”</blockquote>
            <figcaption className="mt-4 text-sm text-ash-400">
              {r.name}, {r.area}
            </figcaption>
          </figure>
        ))}
      </div>
      <style>{`@keyframes marquee { to { transform: translateX(-50%); } }`}</style>
    </section>
  );
}
