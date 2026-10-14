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
  if (typeof body.rider === "string") patch.rider = body.rider.trim().slice(0, 60);

  try {
    const order = await orders().update(id, patch);
    if (!order) return NextResponse.json({ error: "Not found" }, { status: 404 });
    return NextResponse.json({ order });
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 409 });
  }
}
