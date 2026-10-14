import { Plus } from "@phosphor-icons/react/dist/ssr";
import { faqs } from "@/lib/catalog";
import { Container } from "../ui";

export function Faq() {
  return (
    <section id="faq" className="scroll-mt-16 py-24 md:py-32">
      <Container className="grid gap-12 md:grid-cols-[1fr_1.6fr]">
        <h2 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">Questions, answered</h2>
        <div className="divide-y divide-white/[0.06] border-y border-white/[0.06]">
          {faqs.map((f) => (
            <details key={f.q} className="group py-2">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-6 py-4 text-lg font-medium text-ash-50 [&::-webkit-details-marker]:hidden">
                {f.q}
                <Plus
                  size={20}
                  className="shrink-0 text-ash-400 transition-transform duration-300 group-open:rotate-45 group-open:text-ember-400"
                />
              </summary>
              <p className="max-w-[60ch] pb-5 leading-relaxed text-ash-300">{f.a}</p>
            </details>
          ))}
        </div>
      </Container>
    </section>
  );
}
