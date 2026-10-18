import "server-only";
import { createHmac, randomUUID } from "node:crypto";

/**
 * Sends website events to the TechDeva platform dashboard (online orders module).
 *   PLATFORM_WEBHOOK_URL     e.g. https://api.techdeva.net/storefront/webhook/<tenant id>
 *   PLATFORM_WEBHOOK_SECRET  shown once when the owner connects the site on the platform
 *
 * Signature: X-Storefront-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>
 * The platform refuses anything older than 5 minutes, and upserts orders by order
 * number, so retries and re-syncs are always safe.
 */
export type PlatformEvent = "order.created" | "order.updated" | "enquiry.created" | "contact.created" | "catalog.updated";

export const platformConfigured = () => Boolean(process.env.PLATFORM_WEBHOOK_URL && process.env.PLATFORM_WEBHOOK_SECRET);

export function signBody(secret: string, body: string, ts = Math.floor(Date.now() / 1000)): string {
  const mac = createHmac("sha256", secret).update(`${ts}.${body}`).digest("hex");
  return `t=${ts},v1=${mac}`;
}

type Result = { ok: boolean; status: number; retry: boolean };

/** One attempt. Retry only on network errors, 5xx and 429; a 4xx means the payload itself is wrong. */
export async function deliver(type: PlatformEvent, data: Record<string, unknown>): Promise<Result> {
  if (!platformConfigured()) return { ok: false, status: 0, retry: false };
  const body = JSON.stringify({ id: randomUUID(), type, occurred_at: new Date().toISOString(), data });
  try {
    const res = await fetch(process.env.PLATFORM_WEBHOOK_URL!, {
      method: "POST",
      headers: { "content-type": "application/json", "x-storefront-signature": signBody(process.env.PLATFORM_WEBHOOK_SECRET!, body) },
      body,
      signal: AbortSignal.timeout(8000),
    });
    return { ok: res.ok, status: res.status, retry: res.status >= 500 || res.status === 429 };
  } catch {
    return { ok: false, status: 0, retry: true };
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Delivers with backoff (0s, 2s, 8s, 20s). Each attempt is freshly signed, so the
 * 5-minute window never expires mid-retry. Never throws. Call it inside after().
 */
export async function sendToPlatform(type: PlatformEvent, data: Record<string, unknown>, delays = [0, 2000, 8000, 20000]) {
  if (!platformConfigured()) return false;
  for (const wait of delays) {
    if (wait) await sleep(wait);
    const r = await deliver(type, data);
    if (r.ok) return true;
    if (!r.retry) {
      console.error(`platform rejected ${type} (${r.status})`);
      return false;
    }
  }
  console.error(`platform unreachable, gave up on ${type}; use "Sync to TechDeva" in admin to resend`);
  return false;
}

/**
 * The whole menu as customers are charged for it (catalog + owner's overrides),
 * including hidden items, so the dashboard's Products match the site.
 * Sent after every menu edit and by "Sync to TechDeva". Never throws.
 */
export async function sendCatalog(): Promise<boolean> {
  if (!platformConfigured()) return false;
  const { getFullMenu } = await import("./menu");
  const { products } = await getFullMenu();
  return sendToPlatform("catalog.updated", {
    products: products.map((p) => ({
      slug: p.slug, name: p.name, category: p.category, price: p.price,
      description: p.description, active: p.active, soldOut: Boolean(p.soldOut),
    })),
  });
}
