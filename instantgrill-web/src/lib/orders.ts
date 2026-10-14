import "server-only";
import { randomInt, timingSafeEqual } from "node:crypto";
import type { SlotChoice } from "./delivery";
import { quote } from "./pricing";
import { orders, type Order } from "./store";
import type { CartLine } from "./pricing";
import type { CustomerInput } from "./validation";

// No 0/O/1/I so an ID read out over the phone is unambiguous.
const ID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ";

export function newOrderId(): string {
  let s = "";
  for (let i = 0; i < 6; i++) s += ID_ALPHABET[randomInt(ID_ALPHABET.length)];
  return `IG-${s}`;
}

export async function placeOrder(input: {
  customer: CustomerInput;
  cart: CartLine[];
  slot: SlotChoice;
  source?: Order["source"];
}): Promise<Order> {
  const q = quote(input.cart);
  const now = new Date().toISOString();
  const order: Order = {
    id: newOrderId(),
    createdAt: now,
    status: "new",
    customer: input.customer,
    lines: q.lines,
    subtotal: q.subtotal,
    deliveryFee: q.deliveryFee,
    total: q.total,
    slot: input.slot,
    paymentMethod: "cod",
    history: [{ status: "new", at: now }],
    source: input.source,
  };
  await orders().create(order);
  return order;
}

/**
 * Forwards a new order to ORDER_WEBHOOK_URL (Google Sheet script, WhatsApp
 * agent, Slack, owner email...). Fire-and-forget: a slow or broken
 * webhook must never fail a customer's checkout.
 */
export function notifyNewOrder(order: Order): void {
  const url = process.env.ORDER_WEBHOOK_URL;
  if (!url) return;
  fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(process.env.ORDER_WEBHOOK_SECRET ? { "x-webhook-secret": process.env.ORDER_WEBHOOK_SECRET } : {}),
    },
    body: JSON.stringify({ event: "order.created", order }),
    signal: AbortSignal.timeout(5000),
  }).catch((err) => console.error("order webhook failed", order.id, err));
}

/** Public view for the confirmation and tracking pages: area only, masked phone. */
export function publicOrder(o: Order) {
  return {
    id: o.id,
    createdAt: o.createdAt,
    status: o.status,
    lines: o.lines,
    subtotal: o.subtotal,
    deliveryFee: o.deliveryFee,
    total: o.total,
    slot: o.slot,
    area: o.customer.area,
    firstName: o.customer.name.split(" ")[0],
    phoneMasked: o.customer.phone.slice(0, 4) + "*****" + o.customer.phone.slice(-2),
    history: o.history.map(({ status, at }) => ({ status, at })),
  };
}

export type PublicOrder = ReturnType<typeof publicOrder>;

export function isAdmin(req: Request): boolean {
  const expected = process.env.ADMIN_TOKEN;
  if (!expected || expected.length < 16) return false; // admin is off until a strong token is set
  const given = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

/** Small in-memory fixed-window limiter. Per instance; put a CDN/WAF rule in front for real abuse. */
const hits = new Map<string, { count: number; reset: number }>();
export function rateLimited(key: string, limit: number, windowMs: number): boolean {
  const now = Date.now();
  const entry = hits.get(key);
  if (!entry || entry.reset < now) {
    hits.set(key, { count: 1, reset: now + windowMs });
    if (hits.size > 10_000) for (const [k, v] of hits) if (v.reset < now) hits.delete(k);
    return false;
  }
  entry.count++;
  return entry.count > limit;
}

export function clientIp(req: Request): string {
  return (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim() || "unknown";
}
