"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MarketingPage } from "@/components/MarketingPage";
import { API_BASE } from "@/lib/api";
import { SIGNUP_URL, TRIAL_DAYS } from "@/lib/links";

type Plan = { key: string; name: string; price_pkr: number | null; max_numbers: number; max_conversations: number };

// Shown until the live price list loads (and if it cannot be reached). The
// live list comes from the founder console, so a price changed there shows here.
const FALLBACK: Plan[] = [
  { key: "starter", name: "Starter", price_pkr: 20000, max_numbers: 1, max_conversations: 500 },
  { key: "growth", name: "Growth", price_pkr: 34999, max_numbers: 2, max_conversations: 1500 },
  { key: "pro", name: "Pro", price_pkr: null, max_numbers: 5, max_conversations: 4000 },
];

const n = (v: number) => v.toLocaleString("en-US");

export default function PricingPage() {
  const [plans, setPlans] = useState<Plan[]>(FALLBACK);

  useEffect(() => {
    fetch(`${API_BASE}/plans`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && Array.isArray(d.plans) && d.plans.length) setPlans(d.plans);
      })
      .catch(() => {});
  }, []);

  return (
    <MarketingPage
      eyebrow="Pricing"
      title="Simple monthly plans"
      body={`Every plan includes the full business dashboard, Jarvis and Facebook & Instagram posting. Plans differ in how many WhatsApp numbers your AI agent answers, and how many customer conversations a month. Start with a ${TRIAL_DAYS}-day free trial.`}
    >
      <div className="mt-8 grid gap-4 md:grid-cols-3">
        {plans.map((p) => (
          <div key={p.key} className={`td-card flex flex-col p-6 ${p.key === "growth" ? "ring-1 ring-[var(--accent)]" : ""}`}>
            <div className="flex items-center justify-between">
              <div className="font-semibold text-white">{p.name}</div>
              {p.key === "growth" ? <span className="text-xs text-[var(--accent)]">Most popular</span> : null}
            </div>
            <div className="mt-3 text-2xl font-semibold text-white">
              {p.price_pkr != null ? (
                <>
                  PKR {n(Math.round(p.price_pkr))}
                  <span className="text-sm font-normal text-[var(--muted)]"> / month</span>
                </>
              ) : (
                "Talk to us"
              )}
            </div>
            <ul className="mt-4 space-y-2 text-sm text-[var(--muted)]">
              <li>
                <b className="text-white">{p.max_numbers}</b> WhatsApp number{p.max_numbers === 1 ? "" : "s"} with its own AI agent
              </li>
              <li>
                Up to <b className="text-white">{n(p.max_conversations)}</b> customer conversations a month
              </li>
              <li>Appointments, bills, customers, stock, team and reports</li>
              <li>Jarvis voice assistant and social posting</li>
            </ul>
            <div className="mt-auto pt-6">
              {p.price_pkr != null ? (
                <a href={SIGNUP_URL} className="td-btn-primary inline-block px-4 py-2 text-sm">Start free trial</a>
              ) : (
                <Link href="/demo" className="td-btn-ghost inline-block px-4 py-2 text-sm">Talk to us</Link>
              )}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-6 text-sm text-[var(--muted)]">
        A conversation is one customer who writes to your business on WhatsApp in a month, however many messages they
        send. If you go over, your agent keeps answering and we talk to you about the next plan. After the {TRIAL_DAYS}-day
        trial your dashboard stays locked until the first payment.
      </p>
    </MarketingPage>
  );
}
