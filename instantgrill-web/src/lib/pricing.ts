import { findBundle, product, shipping } from "./catalog";

export type CartLine = { bundleId: string; qty: number };

export type PricedLine = {
  bundleId: string;
  name: string;
  unitPrice: number;
  qty: number;
  lineTotal: number;
};

export type Quote = {
  lines: PricedLine[];
  subtotal: number;
  shipping: number;
  total: number;
};

export class PricingError extends Error {}

/**
 * Prices a cart from the catalog. The server calls this on every order, so a
 * tampered client cart can change what is bought but never what it costs.
 * Duplicate lines for one bundle are merged.
 */
export function quote(cart: CartLine[]): Quote {
  if (!Array.isArray(cart) || cart.length === 0) throw new PricingError("Cart is empty");

  const merged = new Map<string, number>();
  for (const line of cart) {
    if (!line || typeof line.bundleId !== "string") throw new PricingError("Invalid cart line");
    if (!Number.isInteger(line.qty) || line.qty < 1) throw new PricingError("Invalid quantity");
    merged.set(line.bundleId, (merged.get(line.bundleId) ?? 0) + line.qty);
  }

  const lines: PricedLine[] = [];
  for (const [bundleId, qty] of merged) {
    const bundle = findBundle(bundleId);
    if (!bundle) throw new PricingError(`Unknown item: ${bundleId}`);
    if (qty > product.maxQtyPerLine) {
      throw new PricingError(`Maximum ${product.maxQtyPerLine} per item. Message us for bulk orders.`);
    }
    lines.push({
      bundleId,
      name: bundle.name,
      unitPrice: bundle.price,
      qty,
      lineTotal: bundle.price * qty,
    });
  }

  const subtotal = lines.reduce((sum, l) => sum + l.lineTotal, 0);
  const shippingFee = subtotal >= shipping.freeAbove ? 0 : shipping.fee;
  return { lines, subtotal, shipping: shippingFee, total: subtotal + shippingFee };
}

export function formatPKR(amount: number): string {
  return `Rs ${amount.toLocaleString("en-PK")}`;
}
