"use client";

import Link from "next/link";
import { useState } from "react";
import { MarketingPage } from "@/components/MarketingPage";
import { Bullets, Section } from "@/components/Prose";
import { API_BASE } from "@/lib/api";

const FIELD =
  "rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2.5 text-sm text-white placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:outline-none";

/** Data deletion instructions + request form (the URL given to Meta). */
export default function DataDeletionPage() {
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState("");

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const phone = String(form.get("phone") || "").trim();
    const email = String(form.get("email") || "").trim();
    if (!phone && !email) {
      setStatus("error");
      setError("Please leave the phone number or email on the account, so we can find it and confirm with you.");
      return;
    }
    setStatus("sending");
    setError("");
    try {
      const res = await fetch(`${API_BASE}/public/demo-request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic: "data_deletion",
          name: form.get("name"),
          business: form.get("business"),
          phone,
          email,
          message: "DELETE MY DATA: " + String(form.get("message") || "everything"),
          website: form.get("website"),
        }),
      });
      if (res.ok) {
        setStatus("sent");
        return;
      }
      const data = await res.json().catch(() => null);
      setError((data && data.detail) || `Something went wrong (${res.status}). Please try again.`);
      setStatus("error");
    } catch {
      setError("We could not reach our server. Check your connection and try again.");
      setStatus("error");
    }
  }

  return (
    <MarketingPage
      eyebrow="Legal"
      title="Delete your data"
      body="How to remove your data from TechDeva, including data we received from Facebook and Instagram."
      cta={false}
    >
      <Section title="Remove the Facebook connection yourself">
        <Bullets
          items={[
            "In Facebook, open Settings & privacy, then Settings, then Business integrations (or Apps and websites).",
            "Find TechDeva and choose Remove. TechDeva can then no longer post to your Page or read your ads.",
            "In your TechDeva dashboard you can also stop using the connection at any time from Integrations.",
          ]}
        />
      </Section>

      <Section title="Ask us to delete everything">
        <p>
          Send the form below. We confirm the request with you on the phone number or email on the account, then
          delete your business&apos;s data (account, records, conversations, pictures, and the Facebook and Instagram
          tokens we stored) within 30 days, and tell you when it is done. See our{" "}
          <Link href="/privacy" className="text-white underline">Privacy Policy</Link> for what we hold.
        </p>
      </Section>

      <div className="mt-8">
        {status === "sent" ? (
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
            <h2 className="text-lg font-semibold text-white">Request received</h2>
            <p className="mt-2 text-sm text-[var(--muted)]">
              We will contact you to confirm, then delete your data within 30 days.
            </p>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="grid gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-6">
            <input name="name" required minLength={2} placeholder="Your name" className={FIELD} />
            <input name="business" required minLength={2} placeholder="Business name on the account" className={FIELD} />
            <div className="grid gap-3 sm:grid-cols-2">
              <input name="phone" placeholder="Phone on the account" inputMode="tel" className={FIELD} />
              <input name="email" type="email" placeholder="Email on the account" className={FIELD} />
            </div>
            <textarea name="message" rows={3} placeholder="Anything we should know (optional)" className={FIELD} />
            <input name="website" tabIndex={-1} autoComplete="off" className="hidden" aria-hidden="true" />
            {status === "error" ? <p className="text-sm text-red-400">{error}</p> : null}
            <button type="submit" disabled={status === "sending"} className="td-btn-primary px-5 py-3 text-sm font-medium disabled:opacity-60">
              {status === "sending" ? "Sending…" : "Request deletion"}
            </button>
          </form>
        )}
      </div>
    </MarketingPage>
  );
}
