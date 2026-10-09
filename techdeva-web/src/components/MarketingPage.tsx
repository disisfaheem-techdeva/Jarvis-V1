import { CtaBand, SiteFooter } from "@/components/SiteFooter";
import { SiteHeader } from "@/components/SiteHeader";

/** Shared frame for marketing pages: site header, hero band, content, closing CTA, footer. */
export function MarketingPage({
  eyebrow,
  title,
  body,
  children,
  cta = true,
}: {
  eyebrow: string;
  title: string;
  body: string;
  children?: React.ReactNode;
  /** Show the closing "Start free trial / Book a demo" band. */
  cta?: boolean;
  /** Kept for callers; content always uses the full 6xl width now. */
  wide?: boolean;
}) {
  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <SiteHeader />
      <main id="main" className="flex-1">
        <section className="td-ambient relative border-b border-line">
          <div className="mx-auto max-w-6xl px-4 pb-14 pt-14 sm:px-6 sm:pb-20 sm:pt-24">
            <p className="text-label font-medium uppercase text-accent-ink">{eyebrow}</p>
            <h1 className="mt-4 max-w-3xl font-display text-4xl font-semibold tracking-[-0.03em] text-fg sm:text-6xl">
              {title}
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-relaxed text-fg-muted">{body}</p>
          </div>
        </section>
        {children ? <div className="mx-auto max-w-6xl px-4 py-14 sm:px-6 sm:py-20">{children}</div> : null}
        {cta ? <CtaBand /> : null}
      </main>
      <SiteFooter />
    </div>
  );
}
