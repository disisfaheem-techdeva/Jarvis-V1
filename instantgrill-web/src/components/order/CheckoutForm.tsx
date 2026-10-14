"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Clock, LockSimple, WarningCircle } from "@phosphor-icons/react";
import { delivery, findProduct } from "@/lib/catalog";
import { availableSlots, describeSlot } from "@/lib/delivery";
import { formatPKR } from "@/lib/pricing";
import type { FieldErrors } from "@/lib/validation";
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

  // Slots depend on the visitor's clock, so they are only computed in the browser
  // (this component renders a skeleton until the cart has loaded client-side).
  const days = useMemo(() => (cart.hydrated ? availableSlots(new Date()) : []), [cart.hydrated]);
  const [date, setDate] = useState<string>();
  const [slot, setSlot] = useState<string>();
  const day = days.find((d) => d.date === (date ?? days[0]?.date));
  const chosenSlot = slot && day?.slots.some((s) => s.value === slot) ? slot : day?.slots[0]?.value;

  if (!cart.hydrated) {
    return <div className="h-96 animate-pulse rounded-[var(--radius-card)] bg-coal-900" aria-label="Loading cart" />;
  }

  if (cart.lines.length === 0) {
    return (
      <div className="rounded-[var(--radius-card)] bg-coal-900 p-10 text-center ring-1 ring-white/[0.06]">
        <p className="text-lg text-ash-100">Your cart is empty.</p>
        <Link href="/menu" className={buttonClass("primary", "md", "mt-6")}>
          Browse the menu
        </Link>
      </div>
    );
  }

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (submitting) return;
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
            area: fd.get("area"),
            address: fd.get("address"),
            landmark: fd.get("landmark"),
            notes: fd.get("notes"),
          },
          slot: { date: day?.date, slot: chosenSlot },
          cart: cart.lines,
          website: fd.get("website"),
          source,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setErrors(data.fields ?? {});
        setFormError(data.error ?? "Something went wrong. Please try again.");
        setSubmitting(false);
        return;
      }
      try {
        sessionStorage.setItem(`ig.order.${data.order.id}`, JSON.stringify(data.order));
      } catch {
        /* the order page falls back to the lookup form */
      }
      cart.clear();
      router.push(`/order/${data.order.id}`);
      // Stay "submitting" while navigating so the button cannot be pressed twice.
    } catch {
      setFormError("No connection. Check your internet and try again.");
      setSubmitting(false);
    }
  }

  const describedBy = (k: keyof FieldErrors, hint = false) =>
    errors[k] ? `${k}-error` : hint ? `${k}-hint` : undefined;
  const border = (k: keyof FieldErrors) => (errors[k] ? "border-danger" : "border-coal-600");

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-10 lg:grid-cols-[1.3fr_1fr]">
      <div className="space-y-6">
        <h2 className="text-xl font-semibold text-ash-50">Delivery details</h2>
        <Field id="name" label="Full name" error={errors.name}>
          <input id="name" name="name" autoComplete="name" required aria-invalid={!!errors.name} aria-describedby={describedBy("name")} className={`${fieldClass} ${border("name")}`} />
        </Field>
        <Field id="phone" label="Mobile number" error={errors.phone} hint="We call or WhatsApp this number to confirm.">
          <input id="phone" name="phone" type="tel" inputMode="tel" autoComplete="tel" placeholder="0300 1234567" required aria-invalid={!!errors.phone} aria-describedby={describedBy("phone", true)} className={`${fieldClass} ${border("phone")}`} />
        </Field>
        <Field id="area" label={`Area in ${delivery.city}`} error={errors.area}>
          <select id="area" name="area" required defaultValue="" aria-invalid={!!errors.area} aria-describedby={describedBy("area")} className={`${fieldClass} ${border("area")}`}>
            <option value="" disabled>
              Select your area
            </option>
            {delivery.areas.map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </Field>
        <Field id="address" label="Full address" error={errors.address} hint="House number, street, block.">
          <textarea id="address" name="address" autoComplete="street-address" rows={3} required aria-invalid={!!errors.address} aria-describedby={describedBy("address", true)} className={`${fieldClass} h-auto py-3 ${border("address")}`} />
        </Field>
        <Field id="landmark" label="Landmark (optional)">
          <input id="landmark" name="landmark" placeholder="Near the mosque, opposite the park..." className={`${fieldClass} border-coal-600`} />
        </Field>

        <fieldset className="space-y-3">
          <legend className={`text-sm font-medium ${errors.slot ? "text-danger" : "text-ash-100"}`}>Delivery time</legend>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-2">
              <label htmlFor="slot-date" className="text-sm text-ash-400">
                Day
              </label>
              <select id="slot-date" value={day?.date ?? ""} onChange={(e) => setDate(e.target.value)} className={`${fieldClass} border-coal-600`}>
                {days.map((d) => (
                  <option key={d.date} value={d.date}>
                    {d.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-2">
              <label htmlFor="slot-time" className="text-sm text-ash-400">
                Time
              </label>
              <select id="slot-time" value={chosenSlot ?? ""} onChange={(e) => setSlot(e.target.value)} className={`${fieldClass} ${border("slot")}`}>
                {day?.slots.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {errors.slot && <p className="text-sm text-danger">{errors.slot}</p>}
        </fieldset>

        <Field id="notes" label="Order notes (optional)">
          <input id="notes" name="notes" className={`${fieldClass} border-coal-600`} />
        </Field>

        <fieldset>
          <legend className="text-sm font-medium text-ash-100">Payment</legend>
          <div className="mt-3 space-y-2">
            <label className="flex items-center gap-3 rounded-[var(--radius-field)] bg-coal-850 p-4 ring-1 ring-ember-500">
              <input type="radio" name="payment" value="cod" defaultChecked className="size-5 accent-[var(--color-ember-500)]" />
              <span className="text-ash-50">Cash on delivery</span>
            </label>
            {["JazzCash", "Easypaisa"].map((m) => (
              <label key={m} className="flex items-center gap-3 rounded-[var(--radius-field)] bg-coal-850 p-4 opacity-50 ring-1 ring-white/[0.06]">
                <input type="radio" name="payment" value={m} disabled className="size-5" />
                <span className="text-ash-100">{m}</span>
                <span className="ml-auto text-xs text-ash-400">Coming soon</span>
              </label>
            ))}
          </div>
        </fieldset>
        {/* Honeypot for bots. Hidden from people and assistive tech. */}
        <input type="text" name="website" tabIndex={-1} autoComplete="off" aria-hidden className="hidden" />
      </div>

      <aside className="h-fit space-y-6 rounded-[var(--radius-card)] bg-coal-900 p-6 ring-1 ring-white/[0.06] md:p-8 lg:sticky lg:top-24">
        <h2 className="text-xl font-semibold text-ash-50">Order summary</h2>
        <ul className="space-y-3">
          {cart.lines.map((l) => {
            const p = findProduct(l.slug)!;
            return (
              <li key={`${l.slug}-${l.chutney}-${l.salad}`} className="flex justify-between gap-4 text-sm">
                <span className="text-ash-100">
                  {p.name} <span className="text-ash-400">× {l.qty}</span>
                  {l.chutney && (
                    <span className="block text-xs text-ash-400">
                      {l.chutney}, {l.salad} salad
                    </span>
                  )}
                </span>
                <span className="font-mono text-ash-100">{formatPKR(p.price * l.qty)}</span>
              </li>
            );
          })}
        </ul>
        <dl className="space-y-2 border-t border-white/[0.06] pt-4 text-sm">
          <div className="flex justify-between text-ash-300">
            <dt>Subtotal</dt>
            <dd className="font-mono">{formatPKR(cart.subtotal)}</dd>
          </div>
          <div className="flex justify-between gap-4 text-ash-300">
            <dt>Delivery</dt>
            <dd className="text-right">{cart.deliveryFee === null ? "Confirmed on WhatsApp" : formatPKR(cart.deliveryFee)}</dd>
          </div>
          <div className="flex justify-between pt-2 text-lg font-semibold text-ash-50">
            <dt>Pay on delivery{cart.deliveryFee === null && <span className="block text-xs font-normal text-ash-400">plus delivery fee</span>}</dt>
            <dd className="font-mono text-ember-400">{formatPKR(cart.total)}</dd>
          </div>
        </dl>
        {day && chosenSlot && (
          <p className="flex items-start gap-3 rounded-[var(--radius-field)] bg-coal-850 p-4 text-sm text-ash-100">
            <Clock size={20} className="mt-0.5 shrink-0 text-ember-400" />
            Your box will arrive {describeSlot({ date: day.date, slot: chosenSlot })}.
          </p>
        )}
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
