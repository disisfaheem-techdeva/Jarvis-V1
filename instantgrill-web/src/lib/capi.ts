import "server-only";
import { createHash } from "node:crypto";
import type { Order } from "./store/types";

const sha = (v: string) => createHash("sha256").update(v.trim().toLowerCase()).digest("hex");

function cookie(req: Request, name: string): string | undefined {
  return (req.headers.get("cookie") ?? "")
    .split(";")
    .map((c) => c.trim().split("="))
    .find(([k]) => k === name)?.[1];
}

/** Builds the Conversions API Purchase event. Exported for tests. */
export function purchaseEvent(order: Order, req: Request) {
  return {
    event_name: "Purchase",
    event_time: Math.floor(new Date(order.createdAt).getTime() / 1000),
    event_id: order.id, // matches the browser Pixel eventID, so Meta counts it once
    action_source: "website",
    event_source_url: req.headers.get("referer") ?? `https://instantgrill.shop/checkout`,
    user_data: {
      ph: [sha("92" + order.customer.phone.slice(1))],
      fn: [sha(order.customer.name.split(" ")[0] ?? "")],
      ct: [sha("lahore")],
      country: [sha("pk")],
      client_ip_address: (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim() || undefined,
      client_user_agent: req.headers.get("user-agent") ?? undefined,
      fbp: cookie(req, "_fbp"),
      fbc: cookie(req, "_fbc"),
    },
    custom_data: {
      currency: "PKR",
      value: order.total,
      order_id: order.id,
      content_type: "product",
      contents: order.lines.map((l) => ({ id: l.slug, quantity: l.qty, item_price: l.unitPrice })),
    },
  };
}

/** Server-side Purchase, so ad attribution survives ad blockers. Fire-and-forget. */
export function sendPurchase(order: Order, req: Request) {
  const pixel = process.env.NEXT_PUBLIC_META_PIXEL_ID;
  const token = process.env.META_CAPI_TOKEN;
  if (!pixel || !token) return;
  fetch(`https://graph.facebook.com/v21.0/${encodeURIComponent(pixel)}/events?access_token=${encodeURIComponent(token)}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      data: [purchaseEvent(order, req)],
      ...(process.env.META_CAPI_TEST_CODE && { test_event_code: process.env.META_CAPI_TEST_CODE }),
    }),
    signal: AbortSignal.timeout(8000),
  })
    .then(async (r) => !r.ok && console.error("meta capi failed", r.status, await r.text()))
    .catch((err) => console.error("meta capi failed", err));
}
