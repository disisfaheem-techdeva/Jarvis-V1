import type { PricedLine } from "../pricing";
import type { CustomerInput } from "../validation";

export const ORDER_STATUSES = [
  "pending", // placed on the site, not yet confirmed with the customer
  "confirmed", // customer confirmed on call or WhatsApp
  "shipped", // handed to courier
  "delivered",
  "cancelled",
  "returned", // refused at door / RTO
] as const;

export type OrderStatus = (typeof ORDER_STATUSES)[number];

export type StatusEvent = { status: OrderStatus; at: string; note?: string };

export type Order = {
  id: string;
  createdAt: string;
  status: OrderStatus;
  customer: CustomerInput;
  lines: PricedLine[];
  subtotal: number;
  shipping: number;
  total: number;
  paymentMethod: "cod";
  trackingNumber?: string;
  courier?: string;
  history: StatusEvent[];
  source?: { utmSource?: string; utmCampaign?: string; referrer?: string };
};

export type OrderUpdate = {
  status?: OrderStatus;
  note?: string;
  trackingNumber?: string;
  courier?: string;
};

export interface OrderRepository {
  create(order: Order): Promise<void>;
  get(id: string): Promise<Order | null>;
  list(opts?: { status?: OrderStatus; limit?: number }): Promise<Order[]>;
  update(id: string, patch: OrderUpdate): Promise<Order | null>;
}

/** Statuses an order may move to from each status. Keeps the admin honest. */
export const TRANSITIONS: Record<OrderStatus, OrderStatus[]> = {
  pending: ["confirmed", "cancelled"],
  confirmed: ["shipped", "cancelled"],
  shipped: ["delivered", "returned"],
  delivered: [],
  cancelled: [],
  returned: [],
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
  if (patch.trackingNumber !== undefined) next.trackingNumber = patch.trackingNumber || undefined;
  if (patch.courier !== undefined) next.courier = patch.courier || undefined;
  return next;
}
