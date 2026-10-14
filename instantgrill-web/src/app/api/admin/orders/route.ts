import { NextResponse } from "next/server";
import { isAdmin } from "@/lib/orders";
import { ORDER_STATUSES, orders, type OrderStatus } from "@/lib/store";

export async function GET(req: Request) {
  if (!isAdmin(req)) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const status = new URL(req.url).searchParams.get("status") as OrderStatus | null;
  if (status && !ORDER_STATUSES.includes(status)) return NextResponse.json({ error: "Unknown status" }, { status: 400 });
  return NextResponse.json({ orders: await orders().list({ status: status ?? undefined }) });
}
