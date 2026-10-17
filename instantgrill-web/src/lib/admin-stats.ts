import type { Order, OrderStatus } from "./store/types";

const PKT_MS = 5 * 3600_000;
/** Lahore calendar date (YYYY-MM-DD) of an instant. */
export const pktDate = (d: Date | string) => new Date(new Date(d).getTime() + PKT_MS).toISOString().slice(0, 10);

const live = (o: Order) => o.status !== "cancelled";

export function dashboardStats(orders: Order[], now = new Date()) {
  const today = pktDate(now);
  // Week = the last 7 Lahore days including today.
  const weekStart = pktDate(new Date(now.getTime() - 6 * 86400_000));
  const placedToday = orders.filter((o) => pktDate(o.createdAt) === today);
  const thisWeek = orders.filter((o) => pktDate(o.createdAt) >= weekStart);

  const byStatus = {} as Record<OrderStatus, number>;
  for (const o of orders) byStatus[o.status] = (byStatus[o.status] ?? 0) + 1;

  const items = new Map<string, { name: string; qty: number; revenue: number }>();
  for (const o of thisWeek.filter(live)) {
    for (const l of o.lines) {
      const cur = items.get(l.slug) ?? { name: l.name, qty: 0, revenue: 0 };
      cur.qty += l.qty;
      cur.revenue += l.lineTotal;
      items.set(l.slug, cur);
    }
  }

  return {
    ordersToday: placedToday.length,
    revenueToday: placedToday.filter(live).reduce((s, o) => s + o.total, 0),
    revenueWeek: thisWeek.filter(live).reduce((s, o) => s + o.total, 0),
    dueToday: orders.filter((o) => o.slot.date === today && (o.status === "confirmed" || o.status === "preparing" || o.status === "new")).length,
    byStatus,
    topItems: [...items.values()].sort((a, b) => b.qty - a.qty).slice(0, 5),
  };
}

export type Customer = { phone: string; name: string; area: string; orders: number; spent: number; lastOrderAt: string };

/** Customers derived from orders (cancelled orders do not count). Newest first. */
export function customersFrom(orders: Order[]): Customer[] {
  const map = new Map<string, Customer>();
  for (const o of [...orders].sort((a, b) => a.createdAt.localeCompare(b.createdAt))) {
    if (!live(o)) continue;
    const c = map.get(o.customer.phone) ?? { phone: o.customer.phone, name: "", area: "", orders: 0, spent: 0, lastOrderAt: "" };
    c.name = o.customer.name;
    c.area = o.customer.area;
    c.orders += 1;
    c.spent += o.total;
    c.lastOrderAt = o.createdAt;
    map.set(c.phone, c);
  }
  return [...map.values()].sort((a, b) => b.lastOrderAt.localeCompare(a.lastOrderAt));
}
