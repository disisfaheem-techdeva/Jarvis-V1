import Link from "next/link";
import { ArrowRight } from "@phosphor-icons/react/dist/ssr";
import { getMenu } from "@/lib/menu";
import { savingIn } from "@/lib/menu-types";
import { formatPKR } from "@/lib/pricing";
import { Reveal } from "../Reveal";
import { Container } from "../ui";

export async function DealsStrip() {
  const menu = await getMenu();
  const family = menu.products.filter((p) => p.category === "family");
  const deals = menu.products.filter((p) => p.category === "deal");
  const familyFrom = Math.min(...family.map((p) => p.price));
  const tiles = [
    { href: "/deals", name: "Family boxes", serves: 4, price: `From ${formatPKR(familyFrom)}`, saving: null as number | null },
    ...deals.map((d) => ({ href: `/menu/${d.slug}`, name: d.name, serves: d.serves!, price: formatPKR(d.price), saving: savingIn(menu, d) })),
  ];
  return (
    <section id="deals" className="scroll-mt-16 py-24 md:py-32">
      <Container>
        <Reveal>
          <h2 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">Feeding more people?</h2>
        </Reveal>
        <div className="mt-12 grid gap-4 md:grid-cols-[1fr_1fr_1.2fr]">
          {tiles.map((t, i) => (
            <Reveal key={t.name} delay={i * 0.06} className="h-full">
              <Link
                href={t.href}
                className={`group flex h-full flex-col justify-between rounded-[var(--radius-card)] p-8 ring-1 transition hover:-translate-y-1 ${
                  i === tiles.length - 1
                    ? "bg-gradient-to-br from-ember-600/30 via-coal-850 to-coal-850 ring-ember-500/40"
                    : "bg-coal-900 ring-white/[0.06]"
                }`}
              >
                <div>
                  <p className="text-sm text-ash-400">Serves {t.serves}</p>
                  <p className="mt-2 text-2xl font-semibold text-ash-50">{t.name}</p>
                </div>
                <div className="mt-10 flex items-end justify-between">
                  <div>
                    <p className="font-mono text-2xl font-semibold text-ember-400">{t.price}</p>
                    {t.saving && <p className="mt-1 text-sm text-ember-300">Save {formatPKR(t.saving)}</p>}
                  </div>
                  <ArrowRight size={22} className="text-ash-300 transition-transform group-hover:translate-x-1" />
                </div>
              </Link>
            </Reveal>
          ))}
        </div>
      </Container>
    </section>
  );
}
