import type { SlotChoice } from "./delivery";
import type { CartLine } from "./pricing";

export type CustomerInput = {
  name: string;
  phone: string;
  area: string;
  address: string;
  landmark?: string;
  notes?: string;
};

export type FieldErrors = Partial<Record<keyof CustomerInput | "cart" | "slot", string>>;

/**
 * Normalises a Pakistani mobile number to 03XXXXXXXXX.
 * Accepts 03001234567, 3001234567, +923001234567, 923001234567, 0300-1234567.
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

/** Shape checks only. Slot availability and prices are checked by the caller against the clock and catalog. */
export function validateOrder(body: unknown, areaNames: string[]):
  | { ok: true; customer: CustomerInput; cart: CartLine[]; slot: SlotChoice }
  | { ok: false; errors: FieldErrors } {
  const b = (body ?? {}) as Record<string, unknown>;
  const c = (b.customer ?? {}) as Record<string, unknown>;
  const s = (b.slot ?? {}) as Record<string, unknown>;
  const errors: FieldErrors = {};

  const name = clean(c.name, 80);
  if (name.length < 2) errors.name = "Enter your full name";

  const phone = normalizePkMobile(clean(c.phone, 20));
  if (!phone) errors.phone = "Enter a mobile number like 0300 1234567";

  const area = clean(c.area, 60);
  if (!areaNames.includes(area)) errors.area = "Choose your area in Lahore";

  const address = clean(c.address, 300);
  if (address.length < 10) errors.address = "Enter house number, street and block so the rider can find you";

  const landmark = clean(c.landmark, 120) || undefined;
  const notes = clean(c.notes, 300) || undefined;

  const slot: SlotChoice = { date: clean(s.date, 10), slot: clean(s.slot, 7) };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(slot.date) || !slot.slot) errors.slot = "Choose a delivery time";

  const cart: CartLine[] = Array.isArray(b.cart)
    ? b.cart.slice(0, 30).map((l) => {
        const line = (l ?? {}) as Record<string, unknown>;
        return {
          slug: String(line.slug ?? ""),
          qty: Number(line.qty),
          chutney: typeof line.chutney === "string" ? (line.chutney as CartLine["chutney"]) : undefined,
          salad: typeof line.salad === "string" ? (line.salad as CartLine["salad"]) : undefined,
        };
      })
    : [];
  if (cart.length === 0) errors.cart = "Your cart is empty";

  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return { ok: true, customer: { name, phone: phone!, area, address, landmark, notes }, cart, slot };
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export function validateEnquiry(body: unknown) {
  const b = (body ?? {}) as Record<string, unknown>;
  const errors: Record<string, string> = {};
  const name = clean(b.name, 80);
  if (name.length < 2) errors.name = "Enter your name";
  const phone = normalizePkMobile(clean(b.phone, 20));
  if (!phone) errors.phone = "Enter a mobile number like 0300 1234567";
  const type = ["bulk", "corporate", "event"].includes(String(b.type)) ? (b.type as "bulk" | "corporate" | "event") : "event";
  const date = clean(b.date, 10);
  if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date)) errors.date = "Pick a date";
  const peopleRaw = clean(b.people, 5);
  const people = peopleRaw ? Number(peopleRaw) : undefined;
  if (people !== undefined && (!Number.isInteger(people) || people < 1 || people > 5000)) errors.people = "Enter how many people";
  const area = clean(b.area, 60) || undefined;
  const message = clean(b.message, 1000) || undefined;
  if (Object.keys(errors).length) return { ok: false as const, errors };
  return { ok: true as const, data: { type, name, phone: phone!, date: date || undefined, people, area, message } };
}

export function validateContact(body: unknown) {
  const b = (body ?? {}) as Record<string, unknown>;
  const errors: Record<string, string> = {};
  const name = clean(b.name, 80);
  if (name.length < 2) errors.name = "Enter your name";
  const phoneRaw = clean(b.phone, 20);
  const phone = phoneRaw ? normalizePkMobile(phoneRaw) : undefined;
  if (phoneRaw && !phone) errors.phone = "Enter a mobile number like 0300 1234567";
  const email = clean(b.email, 120).toLowerCase() || undefined;
  if (email && !EMAIL.test(email)) errors.email = "Enter a valid email";
  if (!phone && !email && !errors.phone && !errors.email) errors.phone = "Give a phone number or email so we can reply";
  // Messages keep their line breaks.
  const message = typeof b.message === "string" ? b.message.trim().slice(0, 2000) : "";
  if (message.length < 5) errors.message = "Write a short message";
  if (Object.keys(errors).length) return { ok: false as const, errors };
  return { ok: true as const, data: { name, phone: phone ?? undefined, email, message } };
}
