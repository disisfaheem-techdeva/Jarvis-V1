"use client";

import { useEffect, useState } from "react";
import { CheckCircle, Circle, WarningCircle, WhatsappLogo } from "@phosphor-icons/react";
import type { PublicOrder } from "@/lib/orders";
import { formatPKR } from "@/lib/pricing";
import { Button, buttonClass } from "../ui";
import { whatsappLink } from "../WhatsAppFab";

const TIMELINE = [
  { status: "pending", label: "Order placed" },
  { status: "confirmed", label: "Confirmed" },
  { status: "shipped", label: "Shipped" },
  { status: "delivered", label: "Delivered" },
] as const;

const fieldClass =
  "h-12 w-full rounded-[var(--radius-field)] border border-coal-600 bg-coal-850 px-4 text-ash-50 placeholder:text-ash-400 focus:border-ember-500 focus:outline-none";

function Timeline({ order }: { order: PublicOrder }) {
  if (order.status === "cancelled" || order.status === "returned") {
    return (
      <p className="flex items-center gap-2 rounded-[var(--radius-field)] bg-coal-850 p-4 text-ash-300">
        <WarningCircle size={20} className="text-danger" />
        This order was {order.status}. Message us on WhatsApp if that is unexpected.
      </p>
    );
  }
  const reached = TIMELINE.findIndex((t) => t.status === order.status);
  return (
    <ol className="grid grid-cols-4 gap-2">
      {TIMELINE.map((t, i) => {
        const done = i <= reached;
        const at = order.history.find((h) => h.status === t.status)?.at;
        return (
          <li key={t.status} className="flex flex-col gap-2">
            <span className={`h-1 rounded-full ${done ? "bg-ember-500" : "bg-coal-700"}`} />
            <span className={`flex items-center gap-1.5 text-sm ${done ? "text-ash-50" : "text-ash-400"}`}>
              {done ? <CheckCircle size={16} weight="fill" className="text-ember-400" /> : <Circle size={16} />}
              {t.label}
            </span>
            {at && (
              <span className="text-xs text-ash-400">
                {new Date(at).toLocaleDateString("en-PK", { day: "numeric", month: "short" })}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export function OrderDetails({ order, fresh = false }: { order: PublicOrder; fresh?: boolean }) {
  const confirmText = `Assalam o Alaikum, I just placed order ${order.id} for ${formatPKR(order.total)}. Please confirm.`;
  return (
    <div className="space-y-8">
      {fresh && (
        <div className="rounded-[var(--radius-card)] bg-gradient-to-br from-ember-600/25 to-coal-900 p-8 ring-1 ring-ember-500/30">
          <CheckCircle size={40} weight="fill" className="text-ember-400" />
          <h1 className="mt-4 text-3xl font-semibold tracking-[-0.03em] text-ash-50 md:text-4xl">
            Thank you, {order.firstName}. Order received.
          </h1>
          <p className="mt-3 max-w-[52ch] text-ash-300">
            Your order number is <span className="font-mono text-ash-50">{order.id}</span>. Confirm it on WhatsApp
            now and we dispatch it faster.
          </p>
          <a
            href={whatsappLink(confirmText)}
            target="_blank"
            rel="noopener noreferrer"
            className={buttonClass("primary", "lg", "mt-6")}
          >
            <WhatsappLogo size={20} weight="fill" /> Confirm on WhatsApp
          </a>
        </div>
      )}

      <div className="space-y-6 rounded-[var(--radius-card)] bg-coal-900 p-6 ring-1 ring-white/[0.06] md:p-8">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-mono text-lg text-ash-50">{order.id}</h2>
          <span className="text-sm text-ash-400">
            {order.city}, {order.phoneMasked}
          </span>
        </div>
        <Timeline order={order} />
        {order.trackingNumber && (
          <p className="text-sm text-ash-300">
            Courier: {order.courier ?? "Courier"}, tracking number{" "}
            <span className="font-mono text-ash-50">{order.trackingNumber}</span>
          </p>
        )}
        <ul className="space-y-2 border-t border-white/[0.06] pt-5 text-sm">
          {order.lines.map((l) => (
            <li key={l.bundleId} className="flex justify-between">
              <span className="text-ash-100">
                {l.name} <span className="text-ash-400">× {l.qty}</span>
              </span>
              <span className="font-mono">{formatPKR(l.lineTotal)}</span>
            </li>
          ))}
          <li className="flex justify-between text-ash-400">
            <span>Delivery</span>
            <span className="font-mono">{order.shipping ? formatPKR(order.shipping) : "Free"}</span>
          </li>
          <li className="flex justify-between pt-2 text-base font-semibold text-ash-50">
            <span>Cash on delivery</span>
            <span className="font-mono text-ember-400">{formatPKR(order.total)}</span>
          </li>
        </ul>
      </div>
    </div>
  );
}

export function TrackForm({ initialId = "" }: { initialId?: string }) {
  const [order, setOrder] = useState<PublicOrder | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const id = String(fd.get("id") ?? "").trim().toUpperCase();
    const phone = String(fd.get("phone") ?? "");
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/orders/${encodeURIComponent(id)}?phone=${encodeURIComponent(phone)}`);
      const data = await res.json();
      if (!res.ok) {
        setOrder(null);
        setError(data.error);
      } else setOrder(data.order);
    } catch {
      setError("No connection. Try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-8">
      <form onSubmit={onSubmit} className="grid gap-4 rounded-[var(--radius-card)] bg-coal-900 p-6 ring-1 ring-white/[0.06] sm:grid-cols-[1fr_1fr_auto] sm:items-end md:p-8">
        <div className="flex flex-col gap-2">
          <label htmlFor="track-id" className="text-sm font-medium text-ash-100">
            Order number
          </label>
          <input id="track-id" name="id" defaultValue={initialId} placeholder="IG-XXXXXX" required className={`${fieldClass} font-mono uppercase`} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor="track-phone" className="text-sm font-medium text-ash-100">
            Mobile number used
          </label>
          <input id="track-phone" name="phone" type="tel" inputMode="tel" placeholder="0300 1234567" required className={fieldClass} />
        </div>
        <Button type="submit" size="lg" disabled={loading}>
          {loading ? "Checking..." : "Track"}
        </Button>
        {error && (
          <p role="alert" className="flex items-center gap-2 text-sm text-danger sm:col-span-3">
            <WarningCircle size={16} /> {error}
          </p>
        )}
      </form>
      {order && <OrderDetails order={order} />}
    </div>
  );
}

/** Confirmation page: shows the order saved by checkout in this tab, else the lookup form. */
export function OrderConfirmation({ id }: { id: string }) {
  const [order, setOrder] = useState<PublicOrder | null | undefined>(undefined);
  useEffect(() => {
    let found: PublicOrder | null = null;
    try {
      const raw = sessionStorage.getItem(`ig.order.${id}`);
      found = raw ? (JSON.parse(raw) as PublicOrder) : null;
    } catch {
      /* fall through to lookup */
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reading browser-only storage after mount
    setOrder(found);
  }, [id]);

  if (order === undefined) return <div className="h-80 animate-pulse rounded-[var(--radius-card)] bg-coal-900" />;
  if (order) return <OrderDetails order={order} fresh />;
  return (
    <>
      <h1 className="mb-8 text-3xl font-semibold tracking-[-0.03em] text-ash-50">Find your order</h1>
      <TrackForm initialId={id} />
    </>
  );
}
