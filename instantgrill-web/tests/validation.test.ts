import { describe, expect, it } from "vitest";
import { normalizePkMobile, toIntlPk, validateContact, validateEnquiry, validateOrder as validate } from "@/lib/validation";

const validateOrder = (b: unknown) => validate(b, ["DHA", "Gulberg"]);

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
    customer: { name: "Ayesha Khan", phone: "0321 7654321", area: "DHA", address: "House 12, Street 4, Phase 5" },
    cart: [{ slug: "chicken-tikka", qty: 1, chutney: "Imli", salad: "Fresh" }],
    slot: { date: "2026-10-15", slot: "morning" },
  };

  it("accepts and normalises a good order", () => {
    const v = validateOrder(good);
    expect(v.ok).toBe(true);
    if (v.ok) expect(v.customer.phone).toBe("03217654321");
  });

  it("reports every bad field at once", () => {
    const v = validateOrder({ customer: { name: "A", phone: "123", area: "Karachi", address: "short" }, cart: [] });
    expect(v.ok).toBe(false);
    if (!v.ok) expect(Object.keys(v.errors).sort()).toEqual(["address", "area", "cart", "name", "phone", "slot"]);
  });

  it("survives garbage input", () => {
    expect(validateOrder(null).ok).toBe(false);
    expect(validateOrder({ customer: "x", cart: "y" }).ok).toBe(false);
  });
});

describe("validateEnquiry", () => {
  it("accepts a minimal enquiry and normalises the phone", () => {
    const v = validateEnquiry({ name: "Hina Malik", phone: "+92 333 1234567", people: "25", date: "2026-11-02", type: "corporate" });
    expect(v).toMatchObject({ ok: true, data: { phone: "03331234567", people: 25, type: "corporate" } });
  });
  it("rejects bad people counts and unknown types fall back to event", () => {
    expect(validateEnquiry({ name: "Hina", phone: "03331234567", people: "-3" }).ok).toBe(false);
    expect(validateEnquiry({ name: "Hina", phone: "03331234567", type: "hack" })).toMatchObject({ ok: true, data: { type: "event" } });
  });
});

describe("validateContact", () => {
  it("needs a way to reply", () => {
    const v = validateContact({ name: "Ali", message: "Do you deliver to Bahria?" });
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.errors.phone).toMatch(/phone number or email/);
  });
  it("accepts email only and keeps line breaks", () => {
    const v = validateContact({ name: "Ali", email: "ALI@example.com", message: "Hi\nTwo lines" });
    expect(v).toMatchObject({ ok: true, data: { email: "ali@example.com", message: "Hi\nTwo lines" } });
  });
  it("rejects a malformed email", () => {
    expect(validateContact({ name: "Ali", email: "ali@", message: "Hello there" }).ok).toBe(false);
  });
});
