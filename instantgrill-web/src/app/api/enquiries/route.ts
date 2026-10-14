import { randomUUID } from "node:crypto";
import { NextResponse, after } from "next/server";
import { notifyEnquiry } from "@/lib/notify";
import { clientIp, rateLimited } from "@/lib/orders";
import { store, type Enquiry } from "@/lib/store";
import { validateEnquiry } from "@/lib/validation";

export async function POST(req: Request) {
  const body = await req.json().catch(() => null);
  if ((body as { website?: string })?.website) return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  const v = validateEnquiry(body);
  if (!v.ok) return NextResponse.json({ error: "Please fix the highlighted fields", fields: v.errors }, { status: 422 });
  if (rateLimited(`enquiry:${clientIp(req)}`, 10, 60 * 60_000)) {
    return NextResponse.json({ error: "Too many requests. Please message us on WhatsApp." }, { status: 429 });
  }
  const enquiry: Enquiry = { id: randomUUID(), createdAt: new Date().toISOString(), ...v.data };
  await store().enquiries.create(enquiry);
  after(() => notifyEnquiry(enquiry));
  return NextResponse.json({ ok: true }, { status: 201 });
}
