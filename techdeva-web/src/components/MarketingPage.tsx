import { SiteHeader } from "@/components/SiteHeader";
import { CtaBand, SiteFooter } from "@/components/SiteFooter";

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
  cta?: boolean;
}) {
  return (
    <div className="min-h-screen bg-[var(--background)]">
      <SiteHeader />
      <main className="mx-auto max-w-3xl px-5 py-16 sm:py-20">
        <div className="text-sm font-medium uppercase tracking-wider text-[var(--accent)]">{eyebrow}</div>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-white sm:text-4xl">{title}</h1>
        <p className="mt-4 text-[var(--muted)]">{body}</p>
        {children}
      </main>
      {cta ? <div className="px-5"><CtaBand /></div> : null}
      <SiteFooter />
    </div>
  );
}
