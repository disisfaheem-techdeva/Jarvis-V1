import { CheckCircle, XCircle } from "@phosphor-icons/react/dist/ssr";
import { comparison } from "@/lib/catalog";
import { Reveal } from "../Reveal";
import { Container } from "../ui";

export function Compare() {
  return (
    <section className="py-24 md:py-32">
      <Container>
        <Reveal>
          <h2 className="max-w-3xl text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">
            The old angithi had a good run.
          </h2>
        </Reveal>

        <div className="mt-14 grid gap-4 md:grid-cols-[1.25fr_1fr]">
          <Reveal className="h-full">
            <div className="relative h-full overflow-hidden rounded-[var(--radius-card)] bg-gradient-to-br from-ember-600/25 via-coal-850 to-coal-850 p-8 ring-1 ring-ember-500/30 md:p-10">
              <p className="text-lg font-semibold text-ash-50">Instant Grill</p>
              <ul className="mt-8 space-y-5">
                {comparison.us.map((t) => (
                  <li key={t} className="flex items-start gap-3 text-lg text-ash-100">
                    <CheckCircle size={24} weight="fill" className="mt-0.5 shrink-0 text-ember-400" />
                    {t}
                  </li>
                ))}
              </ul>
            </div>
          </Reveal>
          <Reveal delay={0.08} className="h-full">
            <div className="h-full rounded-[var(--radius-card)] bg-coal-900 p-8 ring-1 ring-white/[0.06] md:p-10">
              <p className="text-lg font-semibold text-ash-400">Traditional BBQ</p>
              <ul className="mt-8 space-y-5">
                {comparison.them.map((t) => (
                  <li key={t} className="flex items-start gap-3 text-lg text-ash-400">
                    <XCircle size={24} className="mt-0.5 shrink-0 text-coal-600" />
                    {t}
                  </li>
                ))}
              </ul>
            </div>
          </Reveal>
        </div>
      </Container>
    </section>
  );
}
