import { images } from "@/lib/catalog";
import { MotionImage } from "../MotionImage";
import { ButtonLink, Container } from "../ui";

export function FinalCta() {
  return (
    <section className="relative isolate overflow-hidden">
      <MotionImage
        slot={{ ...images.rooftop, brief: "" }}
        sizes="100vw"
        reveal={false}
        parallax={14}
        className="!absolute inset-0 -z-10 h-full w-full"
      />
      <div className="absolute inset-0 -z-10 bg-gradient-to-r from-coal-950 via-coal-950/80 to-coal-950/30" />
      <Container className="py-28 md:py-40">
        <h2 className="max-w-xl text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-6xl">
          Tonight&apos;s BBQ, already in the box.
        </h2>
        <ButtonLink href="/menu" size="lg" className="mt-10">
          Build your box
        </ButtonLink>
      </Container>
    </section>
  );
}
