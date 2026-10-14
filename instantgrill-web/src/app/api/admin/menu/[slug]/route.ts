import { revalidatePath } from "next/cache";
import { NextResponse } from "next/server";
import { isAdminRequest } from "@/lib/auth";
import { findProduct } from "@/lib/catalog";
import { store, type ProductOverride } from "@/lib/store";

/** Owner edits one product: price, sold out today, or hidden from the menu. */
export async function PATCH(req: Request, { params }: { params: Promise<{ slug: string }> }) {
  if (!(await isAdminRequest(req))) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { slug } = await params;
  if (!findProduct(slug)) return NextResponse.json({ error: "Unknown product" }, { status: 404 });

  const body = (await req.json().catch(() => ({}))) as Record<string, unknown>;
  const patch: ProductOverride = {};
  if (body.price !== undefined) {
    const price = Number(body.price);
    if (!Number.isInteger(price) || price < 1 || price > 1_000_000) {
      return NextResponse.json({ error: "Price must be a whole number of rupees" }, { status: 400 });
    }
    patch.price = price;
  }
  if (typeof body.soldOut === "boolean") patch.soldOut = body.soldOut;
  if (typeof body.active === "boolean") patch.active = body.active;

  const overrides = await store().settings.setOverride(slug, patch);
  revalidatePath("/", "layout"); // menu, prices and cart everywhere pick it up
  return NextResponse.json({ override: overrides[slug] });
}
