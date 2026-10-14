"use client";

import { CheckCircle } from "@phosphor-icons/react";
import { Button } from "../ui";
import { Field, borderFor, fieldClass } from "./Field";
import { useFormPost } from "./useFormPost";

export function ContactForm() {
  const f = useFormPost("/api/contact");

  if (f.done) {
    return (
      <div className="rounded-[var(--radius-card)] bg-coal-900 p-8 ring-1 ring-white/[0.06]">
        <CheckCircle size={36} weight="fill" className="text-ember-400" />
        <p className="mt-4 text-xl font-semibold text-ash-50">Message sent.</p>
        <p className="mt-2 text-ash-300">We reply within opening hours, 12 PM to 12 AM.</p>
      </div>
    );
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        f.submit(Object.fromEntries(new FormData(e.currentTarget)));
      }}
      noValidate
      className="space-y-5 rounded-[var(--radius-card)] bg-coal-900 p-6 ring-1 ring-white/[0.06] md:p-8"
    >
      <Field id="c-name" label="Your name" error={f.errors.name}>
        <input id="c-name" name="name" autoComplete="name" className={`${fieldClass} ${borderFor(f.errors.name)}`} />
      </Field>
      <div className="grid gap-5 sm:grid-cols-2">
        <Field id="c-phone" label="Mobile (optional)" error={f.errors.phone}>
          <input id="c-phone" name="phone" type="tel" inputMode="tel" placeholder="0300 1234567" className={`${fieldClass} ${borderFor(f.errors.phone)}`} />
        </Field>
        <Field id="c-email" label="Email (optional)" error={f.errors.email}>
          <input id="c-email" name="email" type="email" autoComplete="email" className={`${fieldClass} ${borderFor(f.errors.email)}`} />
        </Field>
      </div>
      <Field id="c-message" label="Message" error={f.errors.message}>
        <textarea id="c-message" name="message" rows={4} className={`${fieldClass} h-auto py-3 ${borderFor(f.errors.message)}`} />
      </Field>
      <input type="text" name="website" tabIndex={-1} autoComplete="off" aria-hidden className="hidden" />
      {f.error && <p role="alert" className="text-sm text-danger">{f.error}</p>}
      <Button type="submit" size="lg" disabled={f.busy}>
        {f.busy ? "Sending..." : "Send message"}
      </Button>
    </form>
  );
}
