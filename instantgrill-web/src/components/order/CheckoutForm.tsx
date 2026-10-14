"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LockSimple, Money, WarningCircle } from "@phosphor-icons/react";
import { findBundle, shipping } from "@/lib/catalog";
import { formatPKR } from "@/lib/pricing";
import { CITIES, type FieldErrors } from "@/lib/validation";
import { useCart } from "../cart/CartProvider";
import { Button, buttonClass } from "../ui";

const fieldClass =
  "h-12 w-full rounded-[var(--radius-field)] border bg-coal-850 px-4 text-ash-50 placeholder:text-ash-400 transition-colors focus:border-ember-500 focus:outline-none";

function Field({
  id,
  label,
  error,
  hint,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium text-ash-100">
        {label}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} className="flex items-center gap-1.5 text-sm text-danger">
          <WarningCircle size={16} /> {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-sm text-ash-400">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function CheckoutForm() {
  const cart = useCart();
  const router = useRouter();
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!cart.hydrated) {
    return <div className="h-96 animate-pulse rounded-[var(--radius-card)] bg-coal-900" aria-label="Loading cart" />;
  }

  if (cart.lines.length === 0) {
    return (
      <div className="rounded-[var(--radius-card)] bg-coal-900 p-10 text-center ring-1 ring-white/[0.06]">
        <p className="text-lg text-ash-100">Your cart is empty.</p>
        <Link href="/#shop" className={buttonClass("primary", "md", "mt-6")}>
          Choose a kit
        </Link>
      </div>
    );
  }

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSubmitting(true);
    setFormError(null);
    const fd = new FormData(e.currentTarget);
    let source: Record<string, string> = {};
    try {
      source = JSON.parse(sessionStorage.getItem("ig.source") ?? "{}");
    } catch {
      /* attribution is optional */
    }
    try {
      const res = await fetch("/api/orders", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          customer: {
            name: fd.get("name"),
            phone: fd.get("phone"),
            city: fd.get("city"),
            address: fd.get("address"),
            notes: fd.get("notes"),
          },
          cart: cart.lines,
          website: fd.get("website"),
          source,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setErrors(data.fields ?? {});
        setFormError(data.error ?? "Something went wrong. Please try again.");
        return;
      }
      try {
        sessionStorage.setItem(`ig.order.${data.order.id}`, JSON.stringify(data.order));
      } catch {
        /* the order page falls back to the lookup form */
      }
      cart.clear();
      router.push(`/order/${data.order.id}`);
    } catch {
      setFormError("No connection. Check your internet and try again.");
    } finally {
      setSubmitting(false);
    }
  }

  const describedBy = (k: keyof FieldErrors, hint = false) =>
    errors[k] ? `${k}-error` : hint ? `${k}-hint` : undefined;

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-10 lg:grid-cols-[1.3fr_1fr]">
      <div className="space-y-6">
        <h2 className="text-xl font-semibold text-ash-50">Delivery details</h2>
        <Field id="name" label="Full name" error={errors.name}>
          <input
            id="name"
            name="name"
            autoComplete="name"
            required
            aria-invalid={!!errors.name}
            aria-describedby={describedBy("name")}
            className={`${fieldClass} ${errors.name ? "border-danger" : "border-coal-600"}`}
          />
        </Field>
        <Field id="phone" label="Mobile number" error={errors.phone} hint="We call or WhatsApp this number to confirm.">
          <input
            id="phone"
            name="phone"
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            placeholder="0300 1234567"
            required
            aria-invalid={!!errors.phone}
            aria-describedby={describedBy("phone", true)}
            className={`${fieldClass} ${errors.phone ? "border-danger" : "border-coal-600"}`}
          />
        </Field>
        <Field id="city" label="City" error={errors.city}>
          <select
            id="city"
            name="city"
            required
            defaultValue=""
            aria-invalid={!!errors.city}
            aria-describedby={describedBy("city")}
            className={`${fieldClass} ${errors.city ? "border-danger" : "border-coal-600"}`}
          >
            <option value="" disabled>
              Select your city
            </option>
            {CITIES.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </Field>
        <Field id="address" label="Full address" error={errors.address} hint="House number, street, area and a landmark.">
          <textarea
            id="address"
            name="address"
            autoComplete="street-address"
            rows={3}
            required
            aria-invalid={!!errors.address}
            aria-describedby={describedBy("address", true)}
            className={`${fieldClass} h-auto py-3 ${errors.address ? "border-danger" : "border-coal-600"}`}
          />
        </Field>
        <Field id="notes" label="Order notes (optional)">
          <input id="notes" name="notes" className={`${fieldClass} border-coal-600`} />
        </Field>
        {/* Honeypot for bots. Hidden from people and assistive tech. */}
        <input type="text" name="website" tabIndex={-1} autoComplete="off" aria-hidden className="hidden" />
      </div>

      <aside className="h-fit space-y-6 rounded-[var(--radius-card)] bg-coal-900 p-6 ring-1 ring-white/[0.06] lg:sticky lg:top-24 md:p-8">
        <h2 className="text-xl font-semibold text-ash-50">Order summary</h2>
        <ul className="space-y-3">
          {cart.lines.map((l) => {
            const b = findBundle(l.bundleId)!;
            return (
              <li key={l.bundleId} className="flex justify-between gap-4 text-sm">
                <span className="text-ash-100">
                  {b.name} <span className="text-ash-400">× {l.qty}</span>
                </span>
                <span className="font-mono text-ash-100">{formatPKR(b.price * l.qty)}</span>
              </li>
            );
          })}
        </ul>
        <dl className="space-y-2 border-t border-white/[0.06] pt-4 text-sm">
          <div className="flex justify-between text-ash-300">
            <dt>Subtotal</dt>
            <dd className="font-mono">{formatPKR(cart.subtotal)}</dd>
          </div>
          <div className="flex justify-between text-ash-300">
            <dt>Delivery</dt>
            <dd className="font-mono">{cart.shippingFee ? formatPKR(cart.shippingFee) : "Free"}</dd>
          </div>
          <div className="flex justify-between pt-2 text-lg font-semibold text-ash-50">
            <dt>Pay on delivery</dt>
            <dd className="font-mono text-ember-400">{formatPKR(cart.total)}</dd>
          </div>
        </dl>
        <div className="flex items-start gap-3 rounded-[var(--radius-field)] bg-coal-850 p-4 text-sm text-ash-300">
          <Money size={20} className="mt-0.5 shrink-0 text-ember-400" />
          Cash on delivery. Arrives in {shipping.eta.replace("working days", "days")}.
        </div>
        {(formError || errors.cart) && (
          <p role="alert" className="flex items-start gap-2 text-sm text-danger">
            <WarningCircle size={18} className="mt-0.5 shrink-0" /> {errors.cart ?? formError}
          </p>
        )}
        <Button type="submit" size="lg" className="w-full" disabled={submitting}>
          {submitting ? "Placing order..." : "Place order"}
        </Button>
        <p className="flex items-center justify-center gap-1.5 text-xs text-ash-400">
          <LockSimple size={14} /> Your details are only used to deliver this order.
        </p>
      </aside>
    </form>
  );
}
