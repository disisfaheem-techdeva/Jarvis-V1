import { MarketingPage } from "@/components/MarketingPage";

export default function PlatformPage() {
  return (
    <MarketingPage
      eyebrow="Platform"
      title="One platform, set up for your business"
      body="Your services, prices, hours and departments are settings, not custom software, so a new clinic, salon or academy is running on the same proven platform the day it signs up."
    >
      <ul className="mt-8 space-y-3 text-sm text-[var(--muted)]">
        <li>• Your data is yours alone: every business is kept strictly separate from every other.</li>
        <li>• Your AI employees work for you: they answer, draft and report, but never change a price, spend money or publish without your approval.</li>
        <li>• Owner and staff logins: staff can book and take payments; prices, deletions and reports stay with the owner.</li>
        <li>• Works on your phone as well as your computer.</li>
      </ul>
    </MarketingPage>
  );
}
