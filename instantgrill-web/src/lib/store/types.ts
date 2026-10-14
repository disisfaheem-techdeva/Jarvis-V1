import type { SlotChoice } from "../delivery";
import type { PricedLine } from "../pricing";
import type { CustomerInput } from "../validation";

export const ORDER_STATUSES = [
  "new", // placed on the site, not yet confirmed with the customer
  "confirmed", // customer confirmed on call or WhatsApp
  "preparing", // kitchen is marinating and packing
  "out_for_delivery", // with the rider
  "delivered",
  "cancelled",
] as const;

export const STATUS_LABEL: Record<(typeof ORDER_STATUSES)[number], string> = {
  new: "New",
  confirmed: "Confirmed",
  preparing: "Preparing",
  out_for_delivery: "Out for delivery",
  delivered: "Delivered",
  cancelled: "Cancelled",
};

export type OrderStatus = (typeof ORDER_STATUSES)[number];

export type StatusEvent = { status: OrderStatus; at: string; note?: string };

export type Order = {
  id: string;
  createdAt: string;
  status: OrderStatus;
  customer: CustomerInput;
  lines: PricedLine[];
  subtotal: number;
  /** null until the delivery fee is confirmed; then added by the team. */
  deliveryFee: number | null;
  total: number;
  slot: SlotChoice;
  paymentMethod: "cod";
  rider?: string;
  history: StatusEvent[];
  source?: { utmSource?: string; utmCampaign?: string; referrer?: string };
};

export type OrderUpdate = {
  status?: OrderStatus;
  note?: string;
  rider?: string;
};

export interface OrderRepository {
  create(order: Order): Promise<void>;
  get(id: string): Promise<Order | null>;
  list(opts?: { status?: OrderStatus; limit?: number }): Promise<Order[]>;
  update(id: string, patch: OrderUpdate): Promise<Order | null>;
}

/** Statuses an order may move to from each status. Keeps the admin honest. */
export const TRANSITIONS: Record<OrderStatus, OrderStatus[]> = {
  new: ["confirmed", "cancelled"],
  confirmed: ["preparing", "cancelled"],
  preparing: ["out_for_delivery", "cancelled"],
  out_for_delivery: ["delivered", "cancelled"],
  delivered: [],
  cancelled: [],
};

export function applyUpdate(order: Order, patch: OrderUpdate, now = new Date()): Order {
  const next: Order = { ...order, history: [...order.history] };
  if (patch.status && patch.status !== order.status) {
    if (!TRANSITIONS[order.status].includes(patch.status)) {
      throw new Error(`Cannot move an order from ${order.status} to ${patch.status}`);
    }
    next.status = patch.status;
    next.history.push({ status: patch.status, at: now.toISOString(), note: patch.note });
  }
  if (patch.rider !== undefined) next.rider = patch.rider || undefined;
  return next;
}

// ---- Enquiries, contact messages, menu overrides, delivery areas ----

export type Enquiry = {
  id: string;
  createdAt: string;
  type: "bulk" | "corporate" | "event";
  name: string;
  phone: string;
  date?: string;
  people?: number;
  area?: string;
  message?: string;
};

export type ContactMessage = {
  id: string;
  createdAt: string;
  name: string;
  phone?: string;
  email?: string;
  message: string;
};

/** Owner edits on top of the catalog. Missing fields keep the catalog value. */
export type ProductOverride = { price?: number; soldOut?: boolean; active?: boolean };
export type MenuOverrides = Record<string, ProductOverride>;

/** fee null = not confirmed yet; the team confirms it on the call. */
export type DeliveryArea = { name: string; fee: number | null; active: boolean };

export interface InboxRepository<T> {
  create(item: T): Promise<void>;
  list(limit?: number): Promise<T[]>;
}

export interface SettingsRepository {
  getOverrides(): Promise<MenuOverrides>;
  setOverride(slug: string, patch: ProductOverride): Promise<MenuOverrides>;
  /** null = never edited; callers fall back to the catalog defaults. */
  getAreas(): Promise<DeliveryArea[] | null>;
  setAreas(areas: DeliveryArea[]): Promise<void>;
}

export type Store = {
  orders: OrderRepository;
  enquiries: InboxRepository<Enquiry>;
  messages: InboxRepository<ContactMessage>;
  settings: SettingsRepository;
};
