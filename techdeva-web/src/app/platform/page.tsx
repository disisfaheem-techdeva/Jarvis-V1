import type { Metadata } from "next";
import { MarketingPage } from "@/components/MarketingPage";

export const metadata: Metadata = {
  title: "Platform: everything in one business dashboard",
  description:
    "What TechDeva does: appointments and bills, customer history, WhatsApp AI agent on one or more numbers, invoices, " +
    "stock alerts, team PIN logins and HR, Facebook & Instagram posting, Meta and Google ad results, a weekly growth advisor " +
    "and an ad autopilot that works within your limits.",
  alternates: { canonical: "/platform" },
};

const PARTS = [
  ["Appointments & calendar", "Book by service and staff member, take part payments, and see every balance from the real bill."],
  ["Customers & history", "Every visit, payment, package, note and WhatsApp chat for each customer."],
  ["WhatsApp AI sales agent", "Answers on your business number 24/7 in your customers' language, qualifies them and books. It passes the chat to you when a person should answer; bigger packages cover several numbers."],
  ["Invoices & money", "Bills you can print, payments, expenses, daily closing, and profit and loss."],
  ["Stock", "Every product movement recorded, with low-stock alerts before you run out."],
  ["Team & HR", "Staff records, attendance and HR. Each person logs in with a PIN and sees only their part."],
  ["Facebook & Instagram posting", "Draft, schedule and publish posts with your photos; nothing goes public without your approval."],
  ["Ad results", "Spend, leads, chats and cost per result from Meta and Google ads, refreshed through the day."],
  ["Jarvis voice assistant", "Ask about your figures or tell it to book, reschedule or add an expense, just by talking."],
  ["Growth advisor", "A weekly read of your own numbers: what to fix, and what to do next to grow."],
  ["Ad autopilot", "Pauses campaigns that cost more than your limit and writes new ones (picture, headline, text, hashtags, keywords), launching only within the daily budget you set."],
] as const;

export default function PlatformPage() {
  return (
    <MarketingPage
      eyebrow="Platform"
      title="One platform, set up for your business"
      body="Your services, prices, hours and departments are settings, not custom software, so a new clinic, salon or academy is running on the same proven platform the day it signs up."
    >
      <div className="mt-8 grid gap-4 sm:grid-cols-2">
        {PARTS.map(([t, b]) => (
          <div key={t} className="td-card p-5">
            <h2 className="font-semibold text-white">{t}</h2>
            <p className="mt-1.5 text-sm text-[var(--muted)]">{b}</p>
          </div>
        ))}
      </div>
      <h2 className="mt-12 text-xl font-semibold text-white">How it keeps you in control</h2>
      <ul className="mt-4 space-y-3 text-sm text-[var(--muted)]">
        <li>• Your data is yours alone: every business is kept strictly separate from every other.</li>
        <li>• Your AI employees work for you: they answer, draft and report, and never change a price or spend money beyond the limits you set.</li>
        <li>• Owner and staff logins: staff can book and take payments; prices, deletions and reports stay with the owner.</li>
        <li>• Works on your phone as well as your computer.</li>
      </ul>
    </MarketingPage>
  );
}
