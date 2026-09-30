import type { Metadata } from "next";
import Link from "next/link";
import { SiteHeader } from "@/components/SiteHeader";
import { CtaBand, SiteFooter } from "@/components/SiteFooter";
import { SIGNUP_URL, TRIAL_DAYS } from "@/lib/links";

const FEATURES = [
  { title: "Appointments & bills", body: "Book clients with services, discounts, GST and part payments. Every balance comes from the real bill." },
  { title: "Customers & history", body: "Every visit, payment, package, note and WhatsApp chat for each customer, in one place." },
  { title: "WhatsApp AI agent", body: "Your own agent answers customers on your business number in their language, and you can reply from the dashboard." },
  { title: "Jarvis, your voice assistant", body: "Ask about your figures, change prices or hours, or attach a photo and have a post drafted, just by talking." },
  { title: "Money & stock", body: "Expenses, daily closing, profit and loss, and stock that adds up from every movement." },
  { title: "Team & permissions", body: "Staff, attendance and HR. Each person logs in with their own PIN; front desk can book and take payments, prices and deletions stay with the owner." },
  { title: "Facebook & Instagram posting", body: "Write or dictate a post, add your photo and publish or schedule it to your Page and Instagram from the dashboard." },
  { title: "Live ad results", body: "Spend, leads, WhatsApp chats and cost per result from your Meta and Google ads, next to the bookings they brought." },
  { title: "Growth advisor & ad autopilot", body: "Every week it reads your numbers and says what to fix and what to do next. It can pause ads that cost too much and write new campaigns, within limits you set." },
];

export const metadata: Metadata = {
  title: { absolute: "TechDeva: clinic & salon software with a WhatsApp AI agent" },
  description:
    "The AI business operating system for clinics, salons, gyms and academies in Pakistan and abroad: bookings, bills, customers, " +
    "stock, team, a WhatsApp AI agent that answers and books 24/7, social posting, ad results and a growth advisor. 7-day free trial.",
  alternates: { canonical: "/" },
};

export default function Home() {
  return (
    <div className="min-h-screen bg-[var(--background)]">
      <SiteHeader />

      <section className="mx-auto max-w-4xl px-5 py-20 text-center sm:py-28">
        <div className="mx-auto mb-6 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-[var(--muted)]">
          <span className="h-1.5 w-1.5 rounded-full bg-[var(--success)]" />
          Live in production — The Beauty Aesthetic &amp; Lounge
        </div>
        <h1 className="text-4xl font-semibold tracking-tight text-white sm:text-6xl">
          The AI Business
          <br />
          Operating System
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg text-[var(--muted)]">
          One dashboard for your clinic, salon or academy: bookings, bills, customers, stock, your team,
          a WhatsApp AI agent, social posting, ad results and a growth advisor. The AI does the routine work;
          you set the limits and approve what matters.
        </p>
        <div className="mt-9 flex flex-col justify-center gap-3 sm:flex-row">
          <a href={SIGNUP_URL} className="td-btn-primary px-6 py-3 text-sm font-medium">
            Start your {TRIAL_DAYS}-day free trial
          </a>
          <Link href="/demo" className="td-btn-ghost px-6 py-3 text-sm font-medium">
            Book a demo
          </Link>
        </div>
        <p className="mt-3 text-xs text-[var(--muted)]">No card needed. Set up takes a few minutes.</p>
      </section>

      <section className="mx-auto grid max-w-5xl gap-5 px-5 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map((f) => (
          <div key={f.title} className="td-card p-6">
            <h3 className="font-semibold text-white">{f.title}</h3>
            <p className="mt-2 text-sm text-[var(--muted)]">{f.body}</p>
          </div>
        ))}
      </section>

      <section className="mx-auto mt-20 max-w-5xl px-5">
        <h2 className="text-center text-2xl font-semibold text-white">How it starts</h2>
        <ol className="mt-8 grid gap-5 sm:grid-cols-3">
          {[
            ["1. Sign up", "Your business and your owner login are created straight away."],
            ["2. Tell it about your business", "Services, prices, hours and your logo. Your dashboard uses them immediately."],
            ["3. Connect WhatsApp & Facebook", "Scan a QR code for WhatsApp, and connect your Page to post from the dashboard."],
          ].map(([t, b]) => (
            <li key={t} className="td-card p-6">
              <div className="font-semibold text-white">{t}</div>
              <p className="mt-2 text-sm text-[var(--muted)]">{b}</p>
            </li>
          ))}
        </ol>
      </section>

      <div className="px-5"><CtaBand /></div>
      <SiteFooter />
    </div>
  );
}
