import { NextResponse } from "next/server";
import { isAdmin } from "@/lib/orders";
import { ORDER_STATUSES, orders, type OrderUpdate } from "@/lib/store";

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!isAdmin(req)) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as Record<string, unknown>;

  const patch: OrderUpdate = {};
  if (body.status !== undefined) {
    if (!ORDER_STATUSES.includes(body.status as never)) return NextResponse.json({ error: "Unknown status" }, { status: 400 });
    patch.status = body.status as OrderUpdate["status"];
  }
  if (typeof body.note === "string") patch.note = body.note.slice(0, 300);
  if (typeof body.trackingNumber === "string") patch.trackingNumber = body.trackingNumber.trim().slice(0, 60);
  if (typeof body.courier === "string") patch.courier = body.courier.trim().slice(0, 40);

  try {
    const order = await orders().update(id, patch);
    if (!order) return NextResponse.json({ error: "Not found" }, { status: 404 });
    return NextResponse.json({ order });
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 409 });
  }
}
