import "server-only";
import { delivery, products as catalogProducts, type Product } from "./catalog";
import type { Area, MenuData } from "./menu-types";
import { store, type MenuOverrides } from "./store";

export function applyOverrides(base: Product[], overrides: MenuOverrides): (Product & { active: boolean })[] {
  return base.map((p) => {
    const o = overrides[p.slug] ?? {};
    return {
      ...p,
      price: o.price ?? p.price,
      soldOut: o.soldOut ?? p.soldOut ?? false,
      active: o.active ?? true,
    };
  });
}

export const defaultAreas = (): Area[] => delivery.areas.map((name) => ({ name, fee: delivery.fee, active: true }));

/**
 * Everything, including hidden products and inactive areas (admin view).
 * Falls back to the catalog if the database is unreachable, so the shop still
 * renders (and builds) with the base menu.
 */
export async function getFullMenu(): Promise<{ products: (Product & { active: boolean })[]; areas: Area[] }> {
  try {
    const s = store().settings;
    const [overrides, areas] = await Promise.all([s.getOverrides(), s.getAreas()]);
    return { products: applyOverrides(catalogProducts, overrides), areas: areas ?? defaultAreas() };
  } catch (err) {
    console.error("menu: falling back to catalog defaults", err);
    return { products: applyOverrides(catalogProducts, {}), areas: defaultAreas() };
  }
}

/** What customers see and what orders are priced against. */
export async function getMenu(): Promise<MenuData> {
  const full = await getFullMenu();
  return {
    products: full.products.filter((p) => p.active).map((p) => {
      const copy: Product & { active?: boolean } = { ...p };
      delete copy.active;
      return copy;
    }),
    areas: full.areas.filter((a) => a.active),
  };
}
