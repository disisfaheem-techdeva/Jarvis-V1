import Link from "next/link";
import { MarketingPage } from "@/components/MarketingPage";
import { SIGNUP_URL, TRIAL_DAYS } from "@/lib/links";

export default function PricingPage() {
  return (
    <MarketingPage
      eyebrow="Pricing"
      title="Start free, then a plan that fits your business"
      body={`Every business starts with a ${TRIAL_DAYS}-day free trial of the full dashboard. After that, the plan depends on how many locations, staff and channels you run, so we agree it with you directly.`}
    >
      <div className="mt-8 grid gap-4 sm:grid-cols-2">
        <div className="td-card p-6">
          <div className="font-semibold text-white">Free trial</div>
          <p className="mt-2 text-sm text-[var(--muted)]">
            {TRIAL_DAYS} days, every feature: appointments, bills, customers, stock, team, Jarvis and posting.
          </p>
          <a href={SIGNUP_URL} className="td-btn-primary mt-5 inline-block px-4 py-2 text-sm">Start free trial</a>
        </div>
        <div className="td-card p-6">
          <div className="font-semibold text-white">Your plan</div>
          <p className="mt-2 text-sm text-[var(--muted)]">
            Set with you after the trial, including your own WhatsApp AI agent and done-for-you setup.
          </p>
          <Link href="/demo" className="td-btn-ghost mt-5 inline-block px-4 py-2 text-sm">Talk to us</Link>
        </div>
      </div>
    </MarketingPage>
  );
}
