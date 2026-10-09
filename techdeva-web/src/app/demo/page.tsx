"use client";

import { CalendarCheck, CircleCheck, Clock, Hand, MonitorPlay } from "lucide-react";
import { useState } from "react";

import { SiteFooter } from "@/components/site/SiteFooter";
import { SiteHeader } from "@/components/site/SiteHeader";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { Notice } from "@/components/ui/Notice";
import { API_BASE } from "@/lib/api";

const EXPECT = [
  { Icon: MonitorPlay, text: "The platform running on a real clinic, not a slide deck" },
  { Icon: Hand, text: "What your AI team does on its own, and what waits for you" },
  { Icon: Clock, text: "About 30 minutes, at a time that suits you" },
];

export default function DemoPage() {
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setStatus("sending");
    try {
      const res = await fetch(`${API_BASE}/client-request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.get("name"),
          business: form.get("business"),
          phone: form.get("phone"),
          email: form.get("email"),
        }),
      });
      setStatus(res.ok ? "sent" : "error");
    } catch {
      setStatus("error");
    }
  }

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <SiteHeader />
      <main id="main" className="relative flex-1">
        <div aria-hidden="true" className="td-ambient pointer-events-none absolute inset-x-0 top-0 h-[520px]" />
        <div className="relative mx-auto grid max-w-6xl gap-12 px-4 py-14 sm:px-6 sm:py-20 lg:grid-cols-[1fr_1.05fr] lg:items-start">
          <div>
            <p className="text-label font-medium uppercase text-accent-ink">Demo</p>
            <h1 className="mt-4 font-display text-4xl font-semibold tracking-[-0.03em] text-fg sm:text-6xl">Book a demo</h1>
            <p className="mt-6 max-w-lg text-lg leading-relaxed text-fg-muted">
              Tell us about your business and we&apos;ll reach out to schedule a walkthrough.
            </p>
            <ul className="mt-10 flex flex-col gap-4">
              {EXPECT.map(({ Icon, text }) => (
                <li key={text} className="flex items-start gap-3 text-[15px] text-fg">
                  <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--p-radius-md)] bg-accent-tint text-accent-ink">
                    <Icon className="h-[18px] w-[18px]" aria-hidden="true" />
                  </span>
                  <span className="pt-1.5">{text}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="td-glass td-rim rounded-[var(--p-radius-xl)] p-6 sm:p-8">
            {status === "sent" ? (
              <div role="status" className="flex flex-col items-start gap-4 py-6">
                <span className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-success-tint text-success">
                  <CircleCheck className="h-6 w-6" aria-hidden="true" />
                </span>
                <h2 className="font-display text-2xl font-semibold text-fg">Request received</h2>
                <p className="text-[15px] leading-relaxed text-fg-muted">
                  Thanks — we&apos;ve got your request and will be in touch to pick a time for your walkthrough.
                </p>
                <ButtonLink href="/" variant="secondary">
                  Back to home
                </ButtonLink>
              </div>
            ) : (
              <form onSubmit={onSubmit} className="flex flex-col gap-4">
                <h2 className="flex items-center gap-2 text-lg font-semibold text-fg">
                  <CalendarCheck className="h-5 w-5 text-accent-ink" aria-hidden="true" />
                  Your details
                </h2>
                <Field label="Your name" required>
                  {(p) => <Input {...p} name="name" autoComplete="name" />}
                </Field>
                <Field label="Business name" required>
                  {(p) => <Input {...p} name="business" autoComplete="organization" placeholder="e.g. Your clinic or salon" />}
                </Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Phone" hint="WhatsApp works best">
                    {(p) => <Input {...p} name="phone" type="tel" autoComplete="tel" placeholder="+92 300 0000000" />}
                  </Field>
                  <Field label="Email">
                    {(p) => <Input {...p} name="email" type="email" autoComplete="email" />}
                  </Field>
                </div>
                {status === "error" ? (
                  <Notice tone="danger" title="Not sent" live>
                    Something went wrong — please try again.
                  </Notice>
                ) : null}
                <Button type="submit" size="lg" disabled={status === "sending"} className="mt-2 w-full">
                  {status === "sending" ? "Sending…" : "Request a demo"}
                </Button>
              </form>
            )}
          </div>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}
