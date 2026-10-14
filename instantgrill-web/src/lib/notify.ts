import "server-only";
import { describeSlot } from "./delivery";
import { sendToPlatform } from "./platform";
import { formatPKR } from "./pricing";
import type { ContactMessage, Enquiry, Order } from "./store/types";

/**
 * Owner alerts. All fire-and-forget: a slow or failing email/webhook must never
 * break a customer's checkout or form.
 *   RESEND_API_KEY + OWNER_EMAIL (+ optional RESEND_FROM): email via Resend
 *   ORDER_WEBHOOK_URL (+ optional ORDER_WEBHOOK_SECRET): JSON POST of every event
 */
type Event =
  | { event: "order.created"; order: Order }
  | { event: "enquiry.created"; enquiry: Enquiry }
  | { event: "contact.created"; message: ContactMessage };

async function email(subject: string, text: string): Promise<void> {
  const key = process.env.RESEND_API_KEY;
  const to = process.env.OWNER_EMAIL;
  if (!key || !to) return;
  await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
    body: JSON.stringify({ from: process.env.RESEND_FROM ?? "Instant Grill <orders@instantgrill.shop>", to: [to], subject, text }),
    signal: AbortSignal.timeout(8000),
  })
    .then((r) => !r.ok && console.error("resend failed", r.status))
    .catch((err) => console.error("resend failed", err));
}

async function webhook(payload: Event): Promise<void> {
  const url = process.env.ORDER_WEBHOOK_URL;
  if (!url) return;
  await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(process.env.ORDER_WEBHOOK_SECRET ? { "x-webhook-secret": process.env.ORDER_WEBHOOK_SECRET } : {}),
    },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(5000),
  }).catch((err) => console.error("webhook failed", payload.event, err));
}

export function orderEmailText(o: Order): string {
  return [
    `New order ${o.id}`,
    `Deliver: ${describeSlot(o.slot, new Date(o.createdAt))}`,
    "",
    ...o.lines.map((l) => `${l.qty} x ${l.name}${l.chutney ? ` (${l.chutney}, ${l.salad} salad)` : ""}  ${formatPKR(l.lineTotal)}`),
    "",
    `Total COD: ${formatPKR(o.total)}${o.deliveryFee === null ? " + delivery (confirm fee)" : ""}`,
    "",
    `${o.customer.name}  ${o.customer.phone}`,
    `${o.customer.address}, ${o.customer.area}${o.customer.landmark ? ` (${/^near\b/i.test(o.customer.landmark) ? "" : "near "}${o.customer.landmark})` : ""}`,
    o.customer.notes ? `Note: ${o.customer.notes}` : "",
    "",
    `WhatsApp: https://wa.me/92${o.customer.phone.slice(1)}`,
  ].join("\n");
}

/**
 * Everything that should happen after a new order, as one promise that never
 * rejects. Routes pass it to next/server after() so it finishes even on
 * serverless hosts that freeze the function once the response is sent.
 */
export async function notifyNewOrder(order: Order): Promise<void> {
  await Promise.allSettled([
    email(`New order ${order.id}: ${formatPKR(order.total)}, ${order.customer.area}`, orderEmailText(order)),
    webhook({ event: "order.created", order }),
    sendToPlatform("order.created", { order }),
  ]);
}

/** Status, rider or any other change made in the site admin. */
export async function notifyOrderUpdated(order: Order): Promise<void> {
  await sendToPlatform("order.updated", { order });
}

export async function notifyEnquiry(e: Enquiry): Promise<void> {
  const text = [`${e.type} enquiry`, `${e.name}  ${e.phone}`, e.date && `Date: ${e.date}`, e.people && `People: ${e.people}`, e.area && `Area: ${e.area}`, e.message]
    .filter(Boolean)
    .join("\n");
  await Promise.allSettled([
    email(`Event enquiry: ${e.name}${e.people ? `, ${e.people} people` : ""}`, text),
    webhook({ event: "enquiry.created", enquiry: e }),
    sendToPlatform("enquiry.created", { enquiry: e }),
  ]);
}

export async function notifyContact(m: ContactMessage): Promise<void> {
  await Promise.allSettled([
    email(`Website message from ${m.name}`, [m.name, m.phone, m.email, "", m.message].filter((x) => x !== undefined).join("\n")),
    webhook({ event: "contact.created", message: m }),
    sendToPlatform("contact.created", { message: m }),
  ]);
}
