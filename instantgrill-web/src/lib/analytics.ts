"use client";

/**
 * One call fires both Meta Pixel and GA4 (whichever is configured).
 * Meta names are used as the API; GA4 gets its own event names and shape.
 * Purchase carries the order id as eventID so the Pixel event and the
 * server-side Conversions API event are counted once.
 */
type Item = { id: string; name: string; price: number; qty: number };
type Payload = { value: number; items: Item[]; orderId?: string };

declare global {
  interface Window {
    fbq?: (...args: unknown[]) => void;
    gtag?: (...args: unknown[]) => void;
  }
}

const GA_NAMES = {
  ViewContent: "view_item",
  AddToCart: "add_to_cart",
  InitiateCheckout: "begin_checkout",
  Purchase: "purchase",
} as const;

export function track(event: keyof typeof GA_NAMES, p: Payload) {
  try {
    window.fbq?.(
      "track",
      event,
      {
        value: p.value,
        currency: "PKR",
        content_type: "product",
        content_ids: p.items.map((i) => i.id),
        contents: p.items.map((i) => ({ id: i.id, quantity: i.qty, item_price: i.price })),
        num_items: p.items.reduce((s, i) => s + i.qty, 0),
      },
      p.orderId ? { eventID: p.orderId } : undefined,
    );
    window.gtag?.("event", GA_NAMES[event], {
      currency: "PKR",
      value: p.value,
      ...(p.orderId && { transaction_id: p.orderId }),
      items: p.items.map((i) => ({ item_id: i.id, item_name: i.name, price: i.price, quantity: i.qty })),
    });
  } catch {
    /* analytics must never break the shop */
  }
}
