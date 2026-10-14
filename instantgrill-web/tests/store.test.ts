import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { FileOrderRepository } from "@/lib/store/file";
import { applyUpdate, type Order } from "@/lib/store/types";

function order(id: string, createdAt = new Date().toISOString()): Order {
  return {
    id,
    createdAt,
    status: "new",
    customer: { name: "Bilal Ahmed", phone: "03001234567", area: "Gulberg", address: "House 3B, Block 7, Main Boulevard" },
    lines: [{ slug: "chicken-tikka", name: "Chicken Tikka", unitPrice: 3000, qty: 1, lineTotal: 3000, chutney: "Imli", salad: "Fresh" }],
    subtotal: 3000,
    deliveryFee: null,
    total: 3000,
    slot: { date: "2026-10-15", slot: "morning" },
    paymentMethod: "cod",
    history: [{ status: "new", at: createdAt }],
  };
}

describe("applyUpdate", () => {
  it("follows the allowed status path and records history", () => {
    const o = applyUpdate(order("IG-AAAAAA"), { status: "confirmed", note: "called" });
    expect(o.status).toBe("confirmed");
    expect(o.history.at(-1)).toMatchObject({ status: "confirmed", note: "called" });
  });

  it("walks the rider flow and refuses to skip or reverse steps", () => {
    let o = order("IG-AAAAAA");
    for (const s of ["confirmed", "preparing", "out_for_delivery", "delivered"] as const) o = applyUpdate(o, { status: s });
    expect(o.history.map((h) => h.status)).toEqual(["new", "confirmed", "preparing", "out_for_delivery", "delivered"]);
    expect(() => applyUpdate(order("IG-AAAAAA"), { status: "delivered" })).toThrow();
    expect(() => applyUpdate(o, { status: "new" })).toThrow();
  });
});

describe("FileOrderRepository", () => {
  let dir: string;
  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  it("creates, lists newest first, and updates under concurrency", async () => {
    dir = mkdtempSync(path.join(tmpdir(), "ig-"));
    const repo = new FileOrderRepository(dir);
    await Promise.all(
      Array.from({ length: 20 }, (_, i) => repo.create(order(`IG-${String(i).padStart(6, "0")}`, `2026-01-01T00:00:${String(i).padStart(2, "0")}Z`))),
    );
    const all = await repo.list();
    expect(all).toHaveLength(20);
    expect(all[0].id).toBe("IG-000019");

    await repo.update("IG-000003", { status: "confirmed" });
    expect((await repo.get("IG-000003"))?.status).toBe("confirmed");
    expect(await repo.list({ status: "confirmed" })).toHaveLength(1);
    expect(await repo.update("IG-NOPE00", { status: "confirmed" })).toBeNull();
    await expect(repo.create(order("IG-000001"))).rejects.toThrow();
  });
});
