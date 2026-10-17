import type { Metadata } from "next";
import Link from "next/link";
import { CheckCircle, UsersThree } from "@phosphor-icons/react/dist/ssr";
import { EnquiryForm } from "@/components/forms/EnquiryForm";
import { MotionImage } from "@/components/MotionImage";
import { PageHeader } from "@/components/PageHeader";
import { Reveal } from "@/components/Reveal";
import { Container, buttonClass } from "@/components/ui";
import { getMenu } from "@/lib/menu";
import { findIn, savingIn } from "@/lib/menu-types";
import { formatPKR } from "@/lib/pricing";

export const metadata: Metadata = {
  title: "Family boxes, Dawat and Party deals",
  description: "BBQ boxes for 4, 6 or 8 people in Lahore. Family boxes from Rs 6,000, Dawat Deal for 6 and Party Deal for 8. Catering enquiries for bigger events.",
  alternates: { canonical: "/deals" },
};

export default async function DealsPage() {
  const menu = await getMenu();
  const deals = menu.products.filter((p) => p.category === "deal" || p.category === "family");
  return (
    <>
      <PageHeader title="Deals for 4, 6 and 8" intro="Every box serves 2 and comes with its own grill and coal, so a bigger group simply gets more boxes. Savings are against buying the same boxes one by one." />
      <Container className="grid gap-4 py-14 md:grid-cols-2">
        {deals.map((d, i) => {
          const saving = savingIn(menu, d);
          const inside = d.contains?.map((s) => findIn(menu, s)?.name).filter(Boolean);
          const big = d.category === "deal";
          return (
            <Reveal key={d.slug} delay={(i % 2) * 0.06} className={big ? "md:col-span-2" : ""}>
              <article className={`grid h-full overflow-hidden rounded-[var(--radius-card)] ring-1 ${big ? "bg-gradient-to-br from-ember-600/20 via-coal-900 to-coal-900 ring-ember-500/30 md:grid-cols-[1fr_1.1fr]" : "bg-coal-900 ring-white/[0.06]"}`}>
                {big && <MotionImage slot={d.image} sizes="(min-width: 768px) 45vw, 100vw" className="aspect-[4/3] md:aspect-auto md:h-full" />}
                <div className="flex flex-col p-7 md:p-9">
                  <p className="flex items-center gap-2 text-sm text-ash-400">
                    <UsersThree size={18} className="text-ember-400" /> Serves {d.serves}
                  </p>
                  <h2 className="mt-2 text-2xl font-semibold tracking-[-0.02em] text-ash-50 md:text-3xl">{d.name}</h2>
                  <p className="mt-3 text-ash-300">{d.description}</p>
                  {inside && inside.length > 0 && (
                    <ul className="mt-5 space-y-2">
                      {inside.map((n, j) => (
                        <li key={`${n}-${j}`} className="flex items-center gap-2 text-ash-100">
                          <CheckCircle size={18} weight="fill" className="text-ember-400" /> {n} box
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="mt-auto flex flex-wrap items-end justify-between gap-4 pt-8">
                    <div>
                      <p className="font-mono text-3xl font-semibold text-ember-400">{formatPKR(d.price)}</p>
                      {saving && <p className="mt-1 text-sm text-ember-300">Save {formatPKR(saving)}</p>}
                    </div>
                    {d.soldOut ? (
                      <span className="text-sm text-ash-400">Sold out today</span>
                    ) : (
                      <Link href={`/menu/${d.slug}`} className={buttonClass("primary", "lg")}>
                        Choose
                      </Link>
                    )}
                  </div>
                </div>
              </article>
            </Reveal>
          );
        })}
      </Container>

      <Container id="catering" className="scroll-mt-20 grid gap-10 border-t border-white/[0.06] py-20 lg:grid-cols-[1fr_1.4fr]">
        <Reveal>
          <h2 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50">Office, wedding event or big dawat?</h2>
          <p className="mt-4 max-w-[46ch] text-lg leading-relaxed text-ash-300">Tell us the date and headcount and we will plan the boxes and delivery with you.</p>
        </Reveal>
        <EnquiryForm />
      </Container>
    </>
  );
}
