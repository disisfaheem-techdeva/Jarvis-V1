/**
 * Runs the Postgres store against a real database when TEST_DATABASE_URL is set
 * (apply db/migrations first). Skipped otherwise.
 */
import { Pool } from "pg";
import { afterAll, describe, expect, it } from "vitest";
import { postgresStore } from "@/lib/store/postgres";
import type { Order } from "@/lib/store/types";

const url = process.env.TEST_DATABASE_URL;

describe.skipIf(!url)("Postgres store (integration)", () => {
  const pool = new Pool({ connectionString: url });
  const s = postgresStore(pool);
  const id = `IG-T${Date.now().toString(36).toUpperCase().slice(-5)}`;
  afterAll(async () => {
    await pool.query("DELETE FROM orders WHERE id = $1", [id]);
    await pool.query("DELETE FROM product_overrides WHERE slug = 'test-slug'");
    await pool.end();
  });

  it("creates, reads, lists and moves an order through statuses in a transaction", async () => {
    const now = new Date().toISOString();
    const order: Order = {
      id,
      createdAt: now,
      status: "new",
      customer: { name: "Test Kitchen", phone: "03001234567", area: "Gulberg", address: "House 1, Street 1, Gulberg" },
      lines: [{ slug: "chicken-tikka", name: "Chicken Tikka", unitPrice: 3000, qty: 2, lineTotal: 6000, chutney: "Imli", salad: "Fresh" }],
      subtotal: 6000,
      deliveryFee: null,
      total: 6000,
      slot: { date: "2026-10-15", slot: "morning" },
      paymentMethod: "cod",
      history: [{ status: "new", at: now }],
    };
    await s.orders.create(order);
    expect(await s.orders.get(id)).toMatchObject({ id, total: 6000, slot: { slot: "morning" } });
    expect((await s.orders.list({ status: "new" })).some((o) => o.id === id)).toBe(true);
    const updated = await s.orders.update(id, { status: "confirmed", rider: "Bilal" });
    expect(updated).toMatchObject({ status: "confirmed", rider: "Bilal" });
    await expect(s.orders.update(id, { status: "delivered" })).rejects.toThrow(/Cannot move/);
    const { rows } = await pool.query("SELECT status FROM orders WHERE id = $1", [id]);
    expect(rows[0].status).toBe("confirmed"); // failed transition rolled back
    const cust = await pool.query("SELECT * FROM customers WHERE phone = '03001234567'");
    expect(cust.rows[0]).toMatchObject({ name: "Test Kitchen" });
  });

  it("merges product overrides with COALESCE and replaces delivery areas atomically", async () => {
    await s.settings.setOverride("test-slug", { price: 3100 });
    const o = await s.settings.setOverride("test-slug", { soldOut: true });
    expect(o["test-slug"]).toEqual({ price: 3100, soldOut: true });

    const before = await s.settings.getAreas();
    await s.settings.setAreas([
      { name: "Gulberg", fee: 200, active: true },
      { name: "DHA", fee: null, active: false },
    ]);
    expect(await s.settings.getAreas()).toEqual([
      { name: "Gulberg", fee: 200, active: true },
      { name: "DHA", fee: null, active: false },
    ]);
    await expect(s.settings.setAreas([{ name: "X", fee: -5, active: true }])).rejects.toThrow();
    expect(await s.settings.getAreas()).toHaveLength(2); // rolled back, old list kept
    if (before) await s.settings.setAreas(before);
  });

  it("stores enquiries and contact messages", async () => {
    const eid = `t-${Date.now()}`;
    await s.enquiries.create({ id: eid, createdAt: new Date().toISOString(), type: "event", name: "T", phone: "03001234567", people: 30 });
    expect((await s.enquiries.list(5))[0]).toMatchObject({ id: eid, people: 30 });
    await pool.query("DELETE FROM enquiries WHERE id = $1", [eid]);
  });
});
