import { describe, expect, it } from "vitest";
import { product, shipping } from "@/lib/catalog";
import { PricingError, quote } from "@/lib/pricing";

const [solo] = product.bundles;

describe("quote", () => {
  it("prices from the catalog and adds delivery under the threshold", () => {
    const q = quote([{ bundleId: solo.id, qty: 1 }]);
    expect(q.subtotal).toBe(solo.price);
    expect(q.shipping).toBe(solo.price >= shipping.freeAbove ? 0 : shipping.fee);
    expect(q.total).toBe(q.subtotal + q.shipping);
  });

  it("ships free at or above the threshold", () => {
    const qty = Math.min(product.maxQtyPerLine, Math.ceil(shipping.freeAbove / solo.price));
    expect(quote([{ bundleId: solo.id, qty }]).shipping).toBe(0);
  });

  it("merges duplicate lines", () => {
    const q = quote([
      { bundleId: solo.id, qty: 1 },
      { bundleId: solo.id, qty: 2 },
    ]);
    expect(q.lines).toHaveLength(1);
    expect(q.lines[0].qty).toBe(3);
  });

  it("ignores any client-side price and rejects unknown items and bad quantities", () => {
    expect(quote([{ bundleId: solo.id, qty: 1, price: 1 } as never]).subtotal).toBe(solo.price);
    expect(() => quote([{ bundleId: "free-grill", qty: 1 }])).toThrow(PricingError);
    expect(() => quote([{ bundleId: solo.id, qty: 0 }])).toThrow(PricingError);
    expect(() => quote([{ bundleId: solo.id, qty: 1.5 }])).toThrow(PricingError);
    expect(() => quote([{ bundleId: solo.id, qty: product.maxQtyPerLine + 1 }])).toThrow(PricingError);
    expect(() => quote([])).toThrow(PricingError);
  });
});
