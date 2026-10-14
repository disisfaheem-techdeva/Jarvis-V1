import { NextResponse } from "next/server";
import { clientIp, publicOrder, rateLimited } from "@/lib/orders";
import { orders } from "@/lib/store";
import { normalizePkMobile } from "@/lib/validation";

/**
 * Order lookup for the tracking page. Needs the order ID AND the phone it was
 * placed with, so order IDs alone cannot be enumerated to read other orders.
 */
export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  if (rateLimited(`track:${clientIp(req)}`, 30, 10 * 60_000)) {
    return NextResponse.json({ error: "Too many lookups. Try again in a few minutes." }, { status: 429 });
  }
  const { id } = await params;
  const phone = normalizePkMobile(new URL(req.url).searchParams.get("phone") ?? "");
  const order = phone ? await orders().get(id.toUpperCase()) : null;
  if (!order || order.customer.phone !== phone) {
    return NextResponse.json({ error: "No order found for that ID and phone number" }, { status: 404 });
  }
  return NextResponse.json({ order: publicOrder(order) });
}
