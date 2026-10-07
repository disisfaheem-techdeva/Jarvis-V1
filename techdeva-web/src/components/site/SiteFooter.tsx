import Link from "next/link";

import { Logo } from "@/components/ui/Logo";

import { SITE_NAV } from "./nav";

export function SiteFooter() {
  return (
    <footer className="border-t border-line">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-14 sm:px-6 md:grid-cols-[1.4fr_1fr_1fr]">
        <div className="max-w-sm">
          <Logo />
          <p className="mt-4 text-sm leading-relaxed text-fg-muted">
            The AI business operating system for clinics, salons and academies. Your AI team does
            the work; you approve anything that spends money or goes public.
          </p>
        </div>
        <nav aria-label="Product">
          <h2 className="text-label font-medium uppercase text-fg-subtle">Product</h2>
          <ul className="mt-4 space-y-1">
            {SITE_NAV.map((n) => (
              <li key={n.href}>
                <Link href={n.href} className="inline-flex min-h-9 items-center text-sm text-fg-muted hover:text-fg">
                  {n.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <nav aria-label="Get started">
          <h2 className="text-label font-medium uppercase text-fg-subtle">Get started</h2>
          <ul className="mt-4 space-y-1">
            <li>
              <Link href="/demo" className="inline-flex min-h-9 items-center text-sm text-fg-muted hover:text-fg">
                Book a demo
              </Link>
            </li>
            <li>
              <Link href="/login" className="inline-flex min-h-9 items-center text-sm text-fg-muted hover:text-fg">
                Client log in
              </Link>
            </li>
          </ul>
        </nav>
      </div>
      <div className="border-t border-line">
        <p className="mx-auto max-w-6xl px-4 py-6 text-xs text-fg-subtle sm:px-6">
          © {new Date().getFullYear()} TechDeva.
        </p>
      </div>
    </footer>
  );
}
