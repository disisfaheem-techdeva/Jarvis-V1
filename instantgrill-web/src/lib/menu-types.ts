import type { Product } from "./catalog";

import type { DeliveryArea } from "./store/types";

export type Area = DeliveryArea;

/** The live menu: catalog products with the owner's edits applied, plus delivery areas. */
export type MenuData = { products: Product[]; areas: Area[] };

export function findIn(menu: MenuData, slug: string): Product | undefined {
  return menu.products.find((p) => p.slug === slug);
}

/** Saving versus buying the contained boxes separately, at current prices. null when contents are not fixed. */
export function savingIn(menu: MenuData, p: Product): number | null {
  if (!p.contains || p.category === "mix") return null; // a half/half mix is one box, not two
  let separate = 0;
  for (const slug of p.contains) {
    const part = findIn(menu, slug);
    if (!part) return null;
    separate += part.price;
  }
  return separate > p.price ? separate - p.price : null;
}
