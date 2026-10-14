import { NextResponse } from "next/server";
import { SESSION_COOKIE, adminConfigured, checkCredentials, createSessionToken, sessionCookieOptions } from "@/lib/auth";
import { clientIp, rateLimited } from "@/lib/orders";

export async function POST(req: Request) {
  if (!adminConfigured()) {
    return NextResponse.json({ error: "Admin is not set up. Add ADMIN_EMAIL, ADMIN_PASSWORD and ADMIN_SESSION_SECRET." }, { status: 503 });
  }
  if (rateLimited(`login:${clientIp(req)}`, 8, 15 * 60_000)) {
    return NextResponse.json({ error: "Too many attempts. Try again in 15 minutes." }, { status: 429 });
  }
  const body = (await req.json().catch(() => ({}))) as { email?: string; password?: string };
  if (!checkCredentials(String(body.email ?? ""), String(body.password ?? ""))) {
    return NextResponse.json({ error: "Wrong email or password." }, { status: 401 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, createSessionToken(process.env.ADMIN_EMAIL!.toLowerCase()), sessionCookieOptions);
  return res;
}
