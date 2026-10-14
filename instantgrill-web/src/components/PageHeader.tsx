import { Reveal } from "./Reveal";
import { Container } from "./ui";

export function PageHeader({ title, intro }: { title: string; intro?: string }) {
  return (
    <Container className="pt-14 md:pt-20">
      <Reveal>
        <h1 className="max-w-3xl text-5xl font-semibold tracking-[-0.035em] text-ash-50 md:text-6xl">{title}</h1>
        {intro && <p className="mt-5 max-w-[56ch] text-lg leading-relaxed text-ash-300">{intro}</p>}
      </Reveal>
    </Container>
  );
}
