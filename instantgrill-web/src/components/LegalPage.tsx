import { Container } from "./ui";

export function LegalPage({ title, updated, sections }: { title: string; updated: string; sections: { h: string; p: string }[] }) {
  return (
    <Container className="max-w-3xl py-14 md:py-20">
      <h1 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">{title}</h1>
      <p className="mt-3 text-sm text-ash-400">Last updated {updated}</p>
      <div className="mt-12 space-y-10">
        {sections.map((s) => (
          <section key={s.h}>
            <h2 className="text-xl font-semibold text-ash-50">{s.h}</h2>
            <p className="mt-3 max-w-[65ch] leading-relaxed text-ash-300">{s.p}</p>
          </section>
        ))}
      </div>
    </Container>
  );
}
