"use client";

import { useState } from "react";
import { CheckCircle, WhatsappLogo } from "@phosphor-icons/react";
import { Button, buttonClass } from "../ui";
import { whatsappLink } from "@/lib/whatsapp";
import { Field, borderFor, fieldClass } from "./Field";
import { useFormPost } from "./useFormPost";

export function EnquiryForm() {
  const f = useFormPost("/api/enquiries");
  const [summary, setSummary] = useState("");

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>;
    if (await f.submit(d)) {
      setSummary(
        [`Assalam o Alaikum, I sent an event enquiry on your website.`, `Name: ${d.name}`, d.people && `People: ${d.people}`, d.date && `Date: ${d.date}`, d.area && `Area: ${d.area}`, d.message]
          .filter(Boolean)
          .join("\n"),
      );
    }
  }

  if (f.done) {
    return (
      <div className="rounded-[var(--radius-card)] bg-gradient-to-br from-ember-600/25 to-coal-900 p-8 ring-1 ring-ember-500/30">
        <CheckCircle size={36} weight="fill" className="text-ember-400" />
        <p className="mt-4 text-xl font-semibold text-ash-50">Thanks, we have your enquiry.</p>
        <p className="mt-2 text-ash-300">We will call or WhatsApp you within opening hours (12 PM to 12 AM). Want a faster reply? Send it on WhatsApp too.</p>
        <a href={whatsappLink(summary)} target="_blank" rel="noopener noreferrer" className={buttonClass("primary", "lg", "mt-6")}>
          <WhatsappLogo size={20} weight="fill" /> Send on WhatsApp
        </a>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-5 rounded-[var(--radius-card)] bg-coal-900 p-6 ring-1 ring-white/[0.06] sm:grid-cols-2 md:p-8">
      <Field id="enq-name" label="Your name" error={f.errors.name}>
        <input id="enq-name" name="name" autoComplete="name" className={`${fieldClass} ${borderFor(f.errors.name)}`} />
      </Field>
      <Field id="enq-phone" label="Mobile number" error={f.errors.phone}>
        <input id="enq-phone" name="phone" type="tel" inputMode="tel" placeholder="0300 1234567" className={`${fieldClass} ${borderFor(f.errors.phone)}`} />
      </Field>
      <Field id="enq-type" label="What is it for?">
        <select id="enq-type" name="type" defaultValue="event" className={`${fieldClass} border-coal-600`}>
          <option value="event">Event or party</option>
          <option value="corporate">Office or corporate</option>
          <option value="bulk">Bulk order</option>
        </select>
      </Field>
      <Field id="enq-people" label="Number of people" error={f.errors.people}>
        <input id="enq-people" name="people" inputMode="numeric" placeholder="e.g. 20" className={`${fieldClass} ${borderFor(f.errors.people)}`} />
      </Field>
      <Field id="enq-date" label="Date" error={f.errors.date}>
        <input id="enq-date" name="date" type="date" className={`${fieldClass} ${borderFor(f.errors.date)}`} />
      </Field>
      <Field id="enq-area" label="Area in Lahore">
        <input id="enq-area" name="area" placeholder="e.g. DHA Phase 6" className={`${fieldClass} border-coal-600`} />
      </Field>
      <div className="sm:col-span-2">
        <Field id="enq-message" label="Anything else? (optional)">
          <textarea id="enq-message" name="message" rows={3} className={`${fieldClass} h-auto border-coal-600 py-3`} />
        </Field>
      </div>
      <input type="text" name="website" tabIndex={-1} autoComplete="off" aria-hidden className="hidden" />
      {f.error && <p role="alert" className="text-sm text-danger sm:col-span-2">{f.error}</p>}
      <Button type="submit" size="lg" className="sm:col-span-2 sm:justify-self-start" disabled={f.busy}>
        {f.busy ? "Sending..." : "Send enquiry"}
      </Button>
    </form>
  );
}
