import { ButtonLink, Container } from "@/components/ui";

export default function NotFound() {
  return (
    <Container className="flex min-h-[60dvh] flex-col items-start justify-center py-20">
      <h1 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">This page went up in smoke.</h1>
      <p className="mt-4 text-lg text-ash-300">The link may be old or mistyped.</p>
      <ButtonLink href="/" size="lg" className="mt-8">Back to home</ButtonLink>
    </Container>
  );
}
