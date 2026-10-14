import { NextResponse } from "next/server";
import { clientIp, notifyNewOrder, placeOrder, publicOrder, rateLimited } from "@/lib/orders";
import { PricingError } from "@/lib/pricing";
import { validateOrder } from "@/lib/validation";

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  }

  // Honeypot: real customers never see or fill this field.
  if ((body as { website?: string })?.website) return NextResponse.json({ error: "Invalid request" }, { status: 400 });

  const v = validateOrder(body);
  if (!v.ok) return NextResponse.json({ error: "Please fix the highlighted fields", fields: v.errors }, { status: 422 });

  // Counted only for valid orders, so a customer fixing typos is never locked out.
  // Generous because mobile carriers put many customers behind one IP.
  if (rateLimited(`order:${clientIp(req)}`, 20, 10 * 60_000)) {
    return NextResponse.json({ error: "Too many orders from this connection. Please message us on WhatsApp." }, { status: 429 });
  }

  const src = ((body as { source?: Record<string, unknown> }).source ?? {}) as Record<string, unknown>;
  const str = (x: unknown) => (typeof x === "string" ? x.slice(0, 100) : undefined);

  try {
    const order = await placeOrder({
      customer: v.customer,
      cart: v.cart,
      source: { utmSource: str(src.utmSource), utmCampaign: str(src.utmCampaign), referrer: str(src.referrer) },
    });
    notifyNewOrder(order);
    return NextResponse.json({ order: publicOrder(order) }, { status: 201 });
  } catch (err) {
    if (err instanceof PricingError) return NextResponse.json({ error: err.message, fields: { cart: err.message } }, { status: 422 });
    console.error("order create failed", err);
    return NextResponse.json({ error: "We could not save your order. Please try again or message us on WhatsApp." }, { status: 500 });
  }
}
