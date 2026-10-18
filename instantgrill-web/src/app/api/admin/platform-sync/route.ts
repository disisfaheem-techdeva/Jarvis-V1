import { NextResponse } from "next/server";
import { isAdminRequest } from "@/lib/auth";
import { deliver, platformConfigured, sendCatalog } from "@/lib/platform";
import { store } from "@/lib/store";

export const maxDuration = 60;

/**
 * Sends the menu (products and prices), then resends every order, enquiry and
 * message to the TechDeva dashboard.
 * Safe to press any time: the platform upserts by id and ignores older copies.
 */
export async function POST(req: Request) {
  if (!(await isAdminRequest(req))) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  if (!platformConfigured()) {
    return NextResponse.json({ error: "Not connected. Set PLATFORM_WEBHOOK_URL and PLATFORM_WEBHOOK_SECRET." }, { status: 400 });
  }
  // products first, so the orders that follow link to them
  const catalog = await sendCatalog();
  const s = store();
  const [orders, enquiries, messages] = await Promise.all([s.orders.list({ limit: 5000 }), s.enquiries.list(500), s.messages.list(500)]);
  const jobs = [
    ...orders.map((o) => () => deliver("order.updated", { order: o })),
    ...enquiries.map((e) => () => deliver("enquiry.created", { enquiry: e })),
    ...messages.map((m) => () => deliver("contact.created", { message: m })),
  ];
  let sent = 0;
  let failed = 0;
  // Five at a time: quick, without hammering the platform.
  for (let i = 0; i < jobs.length; i += 5) {
    const results = await Promise.all(jobs.slice(i, i + 5).map((j) => j()));
    for (const r of results) {
      if (r.ok) sent++;
      else failed++;
    }
  }
  return NextResponse.json({ catalog, sent, failed, orders: orders.length, enquiries: enquiries.length, messages: messages.length });
}
