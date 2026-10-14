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
    status: "pending",
    customer: { name: "Bilal Ahmed", phone: "03001234567", city: "Karachi", address: "Flat 3B, Block 7, Clifton" },
    lines: [{ bundleId: "solo", name: "The Grill", unitPrice: 100, qty: 1, lineTotal: 100 }],
    subtotal: 100,
    shipping: 0,
    total: 100,
    paymentMethod: "cod",
    history: [{ status: "pending", at: createdAt }],
  };
}

describe("applyUpdate", () => {
  it("follows the allowed status path and records history", () => {
    const o = applyUpdate(order("IG-AAAAAA"), { status: "confirmed", note: "called" });
    expect(o.status).toBe("confirmed");
    expect(o.history.at(-1)).toMatchObject({ status: "confirmed", note: "called" });
  });

  it("refuses to skip or reverse steps", () => {
    expect(() => applyUpdate(order("IG-AAAAAA"), { status: "delivered" })).toThrow();
    const delivered = { ...order("IG-AAAAAA"), status: "delivered" as const };
    expect(() => applyUpdate(delivered, { status: "pending" })).toThrow();
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
