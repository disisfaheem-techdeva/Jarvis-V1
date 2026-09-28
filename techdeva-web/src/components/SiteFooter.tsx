import Link from "next/link";
import { NAV, SIGNUP_URL, TRIAL_DAYS } from "@/lib/links";

export function CtaBand() {
  return (
    <section className="mx-auto mt-16 max-w-3xl rounded-2xl border border-white/10 bg-white/[0.03] px-6 py-10 text-center">
      <h2 className="text-2xl font-semibold text-white">Run your business from one screen</h2>
      <p className="mx-auto mt-3 max-w-xl text-sm text-[var(--muted)]">
        Bookings, bills, customers, stock, your WhatsApp agent and your social posts, in one dashboard.
        Try it free for {TRIAL_DAYS} days, or talk to us first.
      </p>
      <div className="mt-6 flex flex-col justify-center gap-3 sm:flex-row">
        <a href={SIGNUP_URL} className="td-btn-primary px-6 py-3 text-sm font-medium">Start free trial</a>
        <Link href="/demo" className="td-btn-ghost px-6 py-3 text-sm font-medium">Book a demo</Link>
      </div>
    </section>
  );
}

export function SiteFooter() {
  return (
    <footer className="mt-20 border-t border-white/10">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-5 py-10 text-sm text-[var(--muted)] md:flex-row md:items-start md:justify-between">
        <div>
          <div className="flex items-center gap-2 font-semibold text-white">
            <span className="h-2 w-2 rounded-full bg-[var(--accent)]" />
            TechDeva
          </div>
          <p className="mt-2 max-w-xs">The AI business operating system for clinics, salons and academies.</p>
        </div>
        <nav className="grid grid-cols-2 gap-x-10 gap-y-2 sm:grid-cols-3">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className="hover:text-white">{n.label}</Link>
          ))}
          <Link href="/demo" className="hover:text-white">Book a demo</Link>
          <a href={SIGNUP_URL} className="hover:text-white">Start free trial</a>
          <Link href="/login" className="hover:text-white">Log in</Link>
        </nav>
      </div>
      <div className="flex flex-wrap justify-center gap-x-5 gap-y-1 border-t border-white/5 py-4 text-center text-xs text-[var(--muted)]">
        <span>© {new Date().getFullYear()} TechDeva</span>
        <Link href="/privacy" className="hover:text-white">Privacy Policy</Link>
        <Link href="/terms" className="hover:text-white">Terms of Service</Link>
        <Link href="/data-deletion" className="hover:text-white">Delete your data</Link>
      </div>
    </footer>
  );
}
