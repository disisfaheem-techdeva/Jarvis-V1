import { revalidatePath } from "next/cache";
import { NextResponse } from "next/server";
import { isAdminRequest } from "@/lib/auth";
import { store, type DeliveryArea } from "@/lib/store";

/** Replace the delivery area list (name, fee or null, active). */
export async function PUT(req: Request) {
  if (!(await isAdminRequest(req))) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const body = (await req.json().catch(() => ({}))) as { areas?: unknown };
  if (!Array.isArray(body.areas) || body.areas.length === 0 || body.areas.length > 200) {
    return NextResponse.json({ error: "Send at least one area" }, { status: 400 });
  }
  const seen = new Set<string>();
  const areas: DeliveryArea[] = [];
  for (const raw of body.areas as Record<string, unknown>[]) {
    const name = String(raw?.name ?? "").replace(/\s+/g, " ").trim().slice(0, 60);
    if (!name || seen.has(name.toLowerCase())) return NextResponse.json({ error: `Area names must be unique and not empty (${name || "blank"})` }, { status: 400 });
    seen.add(name.toLowerCase());
    const fee = raw.fee === null || raw.fee === "" || raw.fee === undefined ? null : Number(raw.fee);
    if (fee !== null && (!Number.isInteger(fee) || fee < 0 || fee > 100_000)) {
      return NextResponse.json({ error: `Fee for ${name} must be a whole number, or empty` }, { status: 400 });
    }
    areas.push({ name, fee, active: raw.active !== false });
  }
  if (!areas.some((a) => a.active)) return NextResponse.json({ error: "Keep at least one area active" }, { status: 400 });
  await store().settings.setAreas(areas);
  revalidatePath("/", "layout");
  return NextResponse.json({ areas });
}
