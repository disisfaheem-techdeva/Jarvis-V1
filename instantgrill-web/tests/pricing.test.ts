import { describe, expect, it } from "vitest";
import { findProduct, products, savingFor } from "@/lib/catalog";
import { MAX_QTY_PER_LINE, PricingError, quote } from "@/lib/pricing";

const box = { chutney: "Imli" as const, salad: "Fresh" as const };

describe("menu prices match the owner's price list", () => {
  it.each([
    ["chicken-tikka", 3000], ["chicken-kabab", 3100], ["chicken-shashlik", 3200], ["malai-boti", 3300],
    ["beef-seekh-kabab", 3500], ["beef-bihari-boti", 3800], ["rib-eye-tikka", 3900],
    ["mutton-seekh-kabab", 4200], ["reshmi-boti", 4800], ["namkeen-boti", 4800], ["mutton-champ", 4800],
    ["mix-malai-tikka", 3150], ["mix-tikka-beef-seekh", 3250], ["mix-beef-seekh-bihari", 3650],
    ["mix-malai-mutton-seekh", 3750], ["mix-mutton-seekh-namkeen", 4500], ["trio-mix", 3600],
    ["family-chicken", 6000], ["family-chicken-beef", 6200], ["family-beef", 6900],
    ["family-chicken-mutton", 7100], ["family-mutton", 8500],
    ["dawat-deal", 10000], ["party-deal", 12500],
    ["extra-chicken-skewer", 550], ["extra-beef-skewer", 650], ["extra-mutton-skewer", 800],
    ["drink-375", 100], ["drink-500", 150], ["drink-1l", 200], ["drink-1-5l", 250],
  ])("%s costs %i", (slug, price) => expect(findProduct(slug)?.price).toBe(price));

  it("has no duplicate slugs and every item is priced", () => {
    expect(new Set(products.map((p) => p.slug)).size).toBe(products.length);
    expect(products).toHaveLength(31);
  });

  it("computes deal savings from menu prices", () => {
    expect(savingFor(findProduct("dawat-deal")!)).toBe(3000 + 3500 + 4200 - 10000);
    expect(savingFor(findProduct("party-deal")!)).toBe(3000 * 2 + 3500 + 4200 - 12500);
    expect(savingFor(findProduct("family-mutton")!)).toBe(4200 + 4800 - 8500);
    expect(savingFor(findProduct("family-chicken-beef")!)).toBeNull(); // contents not fixed
    expect(savingFor(findProduct("trio-mix")!)).toBeNull(); // a mix is one box
  });
});

describe("quote", () => {
  it("prices boxes, extras and drinks from the catalog", () => {
    const q = quote([
      { slug: "chicken-tikka", qty: 2, ...box },
      { slug: "extra-beef-skewer", qty: 1 },
      { slug: "drink-1-5l", qty: 1 },
    ]);
    expect(q.subtotal).toBe(2 * 3000 + 650 + 250);
    expect(q.deliveryFee).toBeNull();
    expect(q.total).toBe(q.subtotal);
  });

  it("merges identical lines but keeps different choices apart", () => {
    const q = quote([
      { slug: "malai-boti", qty: 1, ...box },
      { slug: "malai-boti", qty: 1, ...box },
      { slug: "malai-boti", qty: 1, chutney: "Aloo Bukhara", salad: "Russian" },
    ]);
    expect(q.lines).toHaveLength(2);
    expect(q.lines.find((l) => l.chutney === "Imli")?.qty).toBe(2);
  });

  it("requires chutney and salad for boxes and drops them for drinks", () => {
    expect(() => quote([{ slug: "chicken-tikka", qty: 1 }])).toThrow(/chutney/);
    expect(() => quote([{ slug: "chicken-tikka", qty: 1, chutney: "Imli" }])).toThrow(/salad/);
    expect(() => quote([{ slug: "chicken-tikka", qty: 1, chutney: "Ketchup" as never, salad: "Fresh" }])).toThrow(PricingError);
    expect(quote([{ slug: "drink-500", qty: 1, ...box }]).lines[0].chutney).toBeUndefined();
  });

  it("ignores client prices and rejects unknown items and bad quantities", () => {
    expect(quote([{ slug: "chicken-tikka", qty: 1, ...box, price: 1 } as never]).subtotal).toBe(3000);
    expect(() => quote([{ slug: "free-box", qty: 1 }])).toThrow(PricingError);
    expect(() => quote([{ slug: "drink-500", qty: 0 }])).toThrow(PricingError);
    expect(() => quote([{ slug: "drink-500", qty: 1.5 }])).toThrow(PricingError);
    expect(() => quote([{ slug: "drink-500", qty: MAX_QTY_PER_LINE + 1 }])).toThrow(PricingError);
    expect(() => quote([])).toThrow(PricingError);
  });
});
