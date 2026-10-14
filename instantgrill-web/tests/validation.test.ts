import { describe, expect, it } from "vitest";
import { normalizePkMobile, toIntlPk, validateOrder } from "@/lib/validation";

describe("normalizePkMobile", () => {
  it.each([
    ["03001234567", "03001234567"],
    ["0300-1234567", "03001234567"],
    ["0300 123 4567", "03001234567"],
    ["+923001234567", "03001234567"],
    ["923001234567", "03001234567"],
    ["3001234567", "03001234567"],
  ])("accepts %s", (input, out) => expect(normalizePkMobile(input)).toBe(out));

  it.each(["", "0421234567", "0300123456", "04001234567", "abc"])("rejects %s", (input) =>
    expect(normalizePkMobile(input)).toBeNull(),
  );

  it("converts to international format", () => expect(toIntlPk("03001234567")).toBe("923001234567"));
});

describe("validateOrder", () => {
  const good = {
    customer: { name: "Ayesha Khan", phone: "0321 7654321", city: "Lahore", address: "House 12, Street 4, DHA Phase 5" },
    cart: [{ bundleId: "solo", qty: 1 }],
  };

  it("accepts and normalises a good order", () => {
    const v = validateOrder(good);
    expect(v.ok).toBe(true);
    if (v.ok) expect(v.customer.phone).toBe("03217654321");
  });

  it("reports every bad field at once", () => {
    const v = validateOrder({ customer: { name: "A", phone: "123", city: "", address: "short" }, cart: [] });
    expect(v.ok).toBe(false);
    if (!v.ok) expect(Object.keys(v.errors).sort()).toEqual(["address", "cart", "city", "name", "phone"]);
  });

  it("survives garbage input", () => {
    expect(validateOrder(null).ok).toBe(false);
    expect(validateOrder({ customer: "x", cart: "y" }).ok).toBe(false);
  });
});
