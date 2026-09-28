"use client";

import { useState } from "react";
import { MarketingPage } from "@/components/MarketingPage";
import { API_BASE } from "@/lib/api";
import { SIGNUP_URL, TRIAL_DAYS } from "@/lib/links";

const FIELD =
  "rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2.5 text-sm text-white placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:outline-none";

export default function DemoPage() {
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState("");

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const phone = String(form.get("phone") || "").trim();
    const email = String(form.get("email") || "").trim();
    if (!phone && !email) {
      setStatus("error");
      setError("Please leave a phone number or an email so we can reach you.");
      return;
    }
    setStatus("sending");
    setError("");
    try {
      const res = await fetch(`${API_BASE}/public/demo-request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.get("name"),
          business: form.get("business"),
          phone,
          email,
          message: form.get("message"),
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
      eyebrow="Demo"
      title="Book a demo"
      body="Tell us about your business and we'll call or message you to walk you through it."
      cta={false}
    >
      {status === "sent" ? (
        <div className="mt-8 rounded-xl border border-white/10 bg-white/[0.03] p-6">
          <p className="text-sm text-[var(--success)]">Thank you — we have your request and will be in touch soon.</p>
          <p className="mt-3 text-sm text-[var(--muted)]">
            Can&apos;t wait? You can{" "}
            <a className="underline" href={SIGNUP_URL}>start your {TRIAL_DAYS}-day free trial</a> right now.
          </p>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="mt-8 grid gap-4 sm:max-w-md">
          <input name="name" required minLength={2} placeholder="Your name" className={FIELD} />
          <input name="business" required minLength={2} placeholder="Business name" className={FIELD} />
          <input name="phone" type="tel" placeholder="Phone / WhatsApp" className={FIELD} />
          <input name="email" type="email" placeholder="Email" className={FIELD} />
          <textarea name="message" rows={3} placeholder="What would you like help with? (optional)" className={FIELD} />
          {/* hidden from people; bots fill it and are ignored */}
          <input name="website" tabIndex={-1} autoComplete="off" aria-hidden="true" className="hidden" />
          <button type="submit" disabled={status === "sending"} className="td-btn-primary px-4 py-2.5 text-sm disabled:opacity-50">
            {status === "sending" ? "Sending…" : "Request a demo"}
          </button>
          {status === "error" ? <p className="text-sm text-[var(--danger)]">{error}</p> : null}
          <p className="text-xs text-[var(--muted)]">
            Prefer to try it yourself? <a className="underline" href={SIGNUP_URL}>Start a free trial</a>.
          </p>
        </form>
      )}
    </MarketingPage>
  );
}
