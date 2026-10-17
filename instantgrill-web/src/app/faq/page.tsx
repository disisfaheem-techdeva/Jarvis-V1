import type { Metadata } from "next";
import { Plus } from "@phosphor-icons/react/dist/ssr";
import { PageHeader } from "@/components/PageHeader";
import { ButtonLink, Container } from "@/components/ui";
import { faqs } from "@/lib/catalog";
import { faqJsonLd, ldJson } from "@/lib/jsonld";

export const metadata: Metadata = {
  title: "FAQ",
  description: "Answers about the Instant Grill BBQ box: what is inside, how many it serves, delivery in Lahore, grilling time, safety, storage and payment.",
  alternates: { canonical: "/faq" },
};

export default function FaqPage() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={ldJson(faqJsonLd())} />
      <PageHeader title="Questions, answered" />
      <Container className="max-w-3xl py-14">
        <div className="divide-y divide-white/[0.06] border-y border-white/[0.06]">
          {faqs.map((f) => (
            <details key={f.q} className="group py-2">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-6 py-4 text-lg font-medium text-ash-50 [&::-webkit-details-marker]:hidden">
                {f.q}
                <Plus size={20} className="shrink-0 text-ash-400 transition-transform duration-300 group-open:rotate-45 group-open:text-ember-400" />
              </summary>
              <p className="max-w-[60ch] pb-5 leading-relaxed text-ash-300">{f.a}</p>
            </details>
          ))}
        </div>
        <div className="mt-12 rounded-[var(--radius-card)] bg-coal-900 p-8 ring-1 ring-white/[0.06]">
          <p className="text-lg text-ash-100">Still have a question?</p>
          <ButtonLink href="/contact" size="lg" className="mt-5">
            Contact us
          </ButtonLink>
        </div>
      </Container>
    </>
  );
}
