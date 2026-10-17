import { CHUTNEYS, SALADS, isBox, type Chutney, type Salad } from "./catalog";
import { findIn, type MenuData } from "./menu-types";

export const MAX_QTY_PER_LINE = 20;

export type CartLine = { slug: string; qty: number; chutney?: Chutney; salad?: Salad };

export type PricedLine = {
  slug: string;
  name: string;
  unitPrice: number;
  qty: number;
  lineTotal: number;
  chutney?: Chutney;
  salad?: Salad;
};

export type Quote = {
  lines: PricedLine[];
  subtotal: number;
  /** null when the area's fee is not confirmed (or no area yet); the team adds it on the call. */
  deliveryFee: number | null;
  total: number;
};

export class PricingError extends Error {}

/** Same product with the same choices is one line. */
export function lineKey(l: Pick<CartLine, "slug" | "chutney" | "salad">): string {
  return [l.slug, l.chutney ?? "", l.salad ?? ""].join("|");
}

/**
 * Prices a cart against the live menu. The server runs this on every order, so a
 * tampered cart can change what is ordered but never what it costs.
 */
export function quote(cart: CartLine[], menu: MenuData, areaName?: string): Quote {
  if (!Array.isArray(cart) || cart.length === 0) throw new PricingError("Your cart is empty");

  const merged = new Map<string, CartLine>();
  for (const line of cart) {
    if (!line || typeof line.slug !== "string") throw new PricingError("Invalid cart item");
    if (!Number.isInteger(line.qty) || line.qty < 1) throw new PricingError("Invalid quantity");
    const product = findIn(menu, line.slug);
    if (!product) throw new PricingError(`This item is no longer on the menu: ${line.slug}`);
    if (product.soldOut) throw new PricingError(`${product.name} is sold out today`);

    let chutney: Chutney | undefined;
    let salad: Salad | undefined;
    if (isBox(product)) {
      if (!CHUTNEYS.includes(line.chutney as Chutney)) throw new PricingError(`Choose a chutney for ${product.name}`);
      if (!SALADS.includes(line.salad as Salad)) throw new PricingError(`Choose a salad for ${product.name}`);
      chutney = line.chutney;
      salad = line.salad;
    }

    const clean: CartLine = { slug: line.slug, qty: line.qty, chutney, salad };
    const key = lineKey(clean);
    const prev = merged.get(key);
    merged.set(key, prev ? { ...prev, qty: prev.qty + clean.qty } : clean);
  }

  const lines: PricedLine[] = [...merged.values()].map((l) => {
    if (l.qty > MAX_QTY_PER_LINE) throw new PricingError(`Maximum ${MAX_QTY_PER_LINE} per item. Message us for bulk orders.`);
    const p = findIn(menu, l.slug)!;
    return { ...l, name: p.name, unitPrice: p.price, lineTotal: p.price * l.qty };
  });

  const subtotal = lines.reduce((s, l) => s + l.lineTotal, 0);
  const fee = areaName ? (menu.areas.find((a) => a.name === areaName)?.fee ?? null) : null;
  return { lines, subtotal, deliveryFee: fee, total: subtotal + (fee ?? 0) };
}

export function formatPKR(amount: number): string {
  return `Rs ${amount.toLocaleString("en-PK")}`;
}
