import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { customersFrom, dashboardStats, pktDate } from "@/lib/admin-stats";
import type { Order } from "@/lib/store/types";

vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => undefined }) }));

function order(id: string, createdAt: string, patch: Partial<Order> = {}, phone = "03001234567"): Order {
  return {
    id,
    createdAt,
    status: "new",
    customer: { name: "Sana Iqbal", phone, area: "Gulberg", address: "House 1, Street 2, Gulberg" },
    lines: [{ slug: "chicken-tikka", name: "Chicken Tikka", unitPrice: 3000, qty: 1, lineTotal: 3000, chutney: "Imli", salad: "Fresh" }],
    subtotal: 3000,
    deliveryFee: null,
    total: 3000,
    slot: { date: pktDate(createdAt), slot: "19:00" },
    paymentMethod: "cod",
    history: [{ status: "new", at: createdAt }],
    ...patch,
  };
}

describe("dashboardStats", () => {
  const now = new Date("2026-10-14T10:00:00Z"); // 3 PM in Lahore
  it("counts today's orders and revenue in Lahore time, excluding cancelled", () => {
    const s = dashboardStats(
      [
        order("A", "2026-10-14T08:00:00Z"),
        order("B", "2026-10-14T09:00:00Z", { status: "cancelled" }),
        order("C", "2026-10-13T20:30:00Z"), // 1:30 AM on the 14th in Lahore
        order("D", "2026-10-13T18:00:00Z"), // 11 PM on the 13th in Lahore
        order("E", "2026-10-01T10:00:00Z"), // outside the week
      ],
      now,
    );
    expect(s.ordersToday).toBe(3);
    expect(s.revenueToday).toBe(6000);
    expect(s.revenueWeek).toBe(9000);
    expect(s.byStatus).toMatchObject({ new: 4, cancelled: 1 });
    expect(s.topItems[0]).toMatchObject({ name: "Chicken Tikka", qty: 3 });
  });
});

describe("customersFrom", () => {
  it("groups by phone, keeps the latest name, ignores cancelled orders", () => {
    const list = customersFrom([
      order("A", "2026-10-01T10:00:00Z"),
      order("B", "2026-10-05T10:00:00Z", { customer: { name: "Sana I.", phone: "03001234567", area: "DHA", address: "x x x x x x x x" } }),
      order("C", "2026-10-06T10:00:00Z", { status: "cancelled" }, "03111111111"),
    ]);
    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({ name: "Sana I.", area: "DHA", orders: 2, spent: 6000 });
  });
});

describe("owner session tokens", () => {
  beforeEach(() => {
    vi.stubEnv("ADMIN_EMAIL", "owner@instantgrill.shop");
    vi.stubEnv("ADMIN_PASSWORD", "correct horse battery");
    vi.stubEnv("ADMIN_SESSION_SECRET", "x".repeat(40));
  });
  afterEach(() => vi.unstubAllEnvs());

  it("round-trips, expires, and rejects tampering", async () => {
    const auth = await import("@/lib/auth");
    const t = auth.createSessionToken("owner@instantgrill.shop", 1000);
    expect(auth.verifySessionToken(t, 2000)).toBe(true);
    expect(auth.verifySessionToken(t, 1000 + 8 * 24 * 3600_000)).toBe(false); // past 7 days
    const [payload, sig] = t.split(".");
    const forged = Buffer.from(JSON.stringify({ e: "owner@instantgrill.shop", exp: 9e15 })).toString("base64url");
    expect(auth.verifySessionToken(`${forged}.${sig}`, 2000)).toBe(false);
    expect(auth.verifySessionToken(`${payload}.x${sig}`, 2000)).toBe(false);
    expect(auth.verifySessionToken(undefined)).toBe(false);
  });

  it("checks credentials and stays off when not configured", async () => {
    const auth = await import("@/lib/auth");
    expect(auth.checkCredentials(" Owner@InstantGrill.shop ", "correct horse battery")).toBe(true);
    expect(auth.checkCredentials("owner@instantgrill.shop", "wrong")).toBe(false);
    vi.stubEnv("ADMIN_SESSION_SECRET", "short");
    expect(auth.adminConfigured()).toBe(false);
    expect(auth.checkCredentials("owner@instantgrill.shop", "correct horse battery")).toBe(false);
  });

  it("a changed ADMIN_EMAIL logs old sessions out", async () => {
    const auth = await import("@/lib/auth");
    const t = auth.createSessionToken("owner@instantgrill.shop");
    vi.stubEnv("ADMIN_EMAIL", "new@instantgrill.shop");
    expect(auth.verifySessionToken(t)).toBe(false);
  });
});

describe("Meta Conversions API event", () => {
  it("hashes the phone in international format and dedups on the order id", async () => {
    const { purchaseEvent } = await import("@/lib/capi");
    const { createHash } = await import("node:crypto");
    const req = new Request("https://instantgrill.shop/api/orders", {
      method: "POST",
      headers: { cookie: "_fbp=fb.1.123; other=1", "x-forwarded-for": "39.1.2.3, 10.0.0.1", "user-agent": "UA" },
    });
    const e = purchaseEvent(order("IG-ABC234", "2026-10-14T08:00:00Z"), req);
    expect(e.event_id).toBe("IG-ABC234");
    expect(e.user_data.ph[0]).toBe(createHash("sha256").update("923001234567").digest("hex"));
    expect(JSON.stringify(e)).not.toContain("03001234567");
    expect(e.user_data).toMatchObject({ fbp: "fb.1.123", client_ip_address: "39.1.2.3" });
    expect(e.custom_data).toMatchObject({ currency: "PKR", value: 3000 });
  });
});
