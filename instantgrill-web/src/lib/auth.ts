import "server-only";
import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

/**
 * Owner login: one email + password from env, and a signed, httpOnly session
 * cookie. No user table to manage and nothing that can leak to the browser.
 *   ADMIN_EMAIL, ADMIN_PASSWORD (12+ chars), ADMIN_SESSION_SECRET (32+ chars)
 * Admin is switched off until all three are set.
 */
export const SESSION_COOKIE = "ig_admin";
const MAX_AGE_S = 7 * 24 * 3600;

function secret(): string | null {
  const s = process.env.ADMIN_SESSION_SECRET;
  return s && s.length >= 32 ? s : null;
}

export function adminConfigured(): boolean {
  return Boolean(secret() && process.env.ADMIN_EMAIL && (process.env.ADMIN_PASSWORD ?? "").length >= 12);
}

const b64 = (s: string) => Buffer.from(s).toString("base64url");
const sign = (payload: string, key: string) => createHmac("sha256", key).update(payload).digest("base64url");

function safeEqual(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

export function createSessionToken(email: string, now = Date.now()): string {
  const key = secret();
  if (!key) throw new Error("ADMIN_SESSION_SECRET is not set");
  const payload = b64(JSON.stringify({ e: email, exp: now + MAX_AGE_S * 1000 }));
  return `${payload}.${sign(payload, key)}`;
}

export function verifySessionToken(token: string | undefined, now = Date.now()): boolean {
  const key = secret();
  if (!key || !token) return false;
  const [payload, sig] = token.split(".");
  if (!payload || !sig || !safeEqual(sig, sign(payload, key))) return false;
  try {
    const { e, exp } = JSON.parse(Buffer.from(payload, "base64url").toString());
    // A changed ADMIN_EMAIL logs out old sessions.
    return typeof exp === "number" && exp > now && e === process.env.ADMIN_EMAIL?.toLowerCase();
  } catch {
    return false;
  }
}

export function checkCredentials(email: string, password: string): boolean {
  if (!adminConfigured()) return false;
  const okEmail = safeEqual(email.trim().toLowerCase(), process.env.ADMIN_EMAIL!.toLowerCase());
  const okPass = safeEqual(password, process.env.ADMIN_PASSWORD!);
  return okEmail && okPass;
}

export const sessionCookieOptions = {
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax" as const,
  path: "/",
  maxAge: MAX_AGE_S,
};

/** For server components and route handlers. */
export async function isAdminSession(): Promise<boolean> {
  return verifySessionToken((await cookies()).get(SESSION_COOKIE)?.value);
}

/**
 * For admin API routes: valid session, and for writes, a same-origin request
 * (blocks cross-site form posts riding on the cookie).
 */
export async function isAdminRequest(req: Request): Promise<boolean> {
  if (!(await isAdminSession())) return false;
  if (req.method === "GET" || req.method === "HEAD") return true;
  const origin = req.headers.get("origin");
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
  return !!origin && !!host && new URL(origin).host === host;
}
