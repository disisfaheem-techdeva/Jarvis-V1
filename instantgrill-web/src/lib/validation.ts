import type { CartLine } from "./pricing";

export type CustomerInput = {
  name: string;
  phone: string;
  city: string;
  address: string;
  notes?: string;
};

export type FieldErrors = Partial<Record<keyof CustomerInput | "cart", string>>;

export const CITIES = [
  "Lahore",
  "Karachi",
  "Islamabad",
  "Rawalpindi",
  "Faisalabad",
  "Multan",
  "Peshawar",
  "Gujranwala",
  "Sialkot",
  "Quetta",
  "Hyderabad",
  "Bahawalpur",
  "Sargodha",
  "Abbottabad",
  "Other",
];

/**
 * Normalises a Pakistani mobile number to 03XXXXXXXXX.
 * Accepts 03001234567, 3001234567, +923001234567, 923001234567, 0300-1234567.
 * Returns null when the number is not a valid PK mobile.
 */
export function normalizePkMobile(raw: string): string | null {
  const digits = String(raw ?? "").replace(/\D/g, "");
  let local: string;
  if (digits.startsWith("92") && digits.length === 12) local = "0" + digits.slice(2);
  else if (digits.startsWith("3") && digits.length === 10) local = "0" + digits;
  else local = digits;
  return /^03\d{9}$/.test(local) ? local : null;
}

/** 03001234567 to 923001234567 for wa.me links. */
export function toIntlPk(local: string): string {
  return "92" + local.slice(1);
}

const clean = (v: unknown, max: number) =>
  typeof v === "string" ? v.replace(/\s+/g, " ").trim().slice(0, max) : "";

export function validateOrder(body: unknown):
  | { ok: true; customer: CustomerInput; cart: CartLine[] }
  | { ok: false; errors: FieldErrors } {
  const b = (body ?? {}) as Record<string, unknown>;
  const c = (b.customer ?? {}) as Record<string, unknown>;
  const errors: FieldErrors = {};

  const name = clean(c.name, 80);
  if (name.length < 2) errors.name = "Enter your full name";

  const phone = normalizePkMobile(clean(c.phone, 20));
  if (!phone) errors.phone = "Enter a mobile number like 0300 1234567";

  const city = clean(c.city, 40);
  if (!city) errors.city = "Choose your city";

  const address = clean(c.address, 300);
  if (address.length < 10) errors.address = "Enter house, street and area so the rider can find you";

  const notes = clean(c.notes, 300) || undefined;

  const cart: CartLine[] = Array.isArray(b.cart)
    ? b.cart.slice(0, 10).map((l) => {
        const line = (l ?? {}) as Record<string, unknown>;
        return { bundleId: String(line.bundleId ?? ""), qty: Number(line.qty) };
      })
    : [];
  if (cart.length === 0) errors.cart = "Your cart is empty";

  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return { ok: true, customer: { name, phone: phone!, city, address, notes }, cart };
}
