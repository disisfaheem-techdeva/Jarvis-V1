"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowClockwise, Printer, WhatsappLogo } from "@phosphor-icons/react";
import { pktDate } from "@/lib/admin-stats";
import { describeSlot } from "@/lib/delivery";
import { formatPKR } from "@/lib/pricing";
import { ORDER_STATUSES, STATUS_LABEL, TRANSITIONS, type Order, type OrderStatus } from "@/lib/store/types";
import { toIntlPk } from "@/lib/validation";
import { Button } from "../ui";

const statusStyle: Record<OrderStatus, string> = {
  new: "bg-ember-500/15 text-ember-300",
  confirmed: "bg-sky-400/15 text-sky-300",
  preparing: "bg-amber-400/15 text-amber-300",
  out_for_delivery: "bg-violet-400/15 text-violet-300",
  delivered: "bg-ok/15 text-ok",
  cancelled: "bg-coal-700 text-ash-400",
};

const field =
  "h-10 rounded-[var(--radius-field)] border border-coal-600 bg-coal-850 px-3 text-sm text-ash-50 placeholder:text-ash-400 focus:border-ember-500 focus:outline-none";

export function OrdersBoard({ initialStatus }: { initialStatus?: OrderStatus }) {
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [status, setStatus] = useState<OrderStatus | "all">(initialStatus ?? "all");
  const [date, setDate] = useState(""); // delivery date filter, YYYY-MM-DD
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    const res = await fetch("/api/admin/orders");
    if (!res.ok) return setError(res.status === 401 ? "Your session ended. Reload and sign in again." : "Could not load orders.");
    setOrders((await res.json()).orders);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch
    load();
  }, [load]);

  async function patch(id: string, body: Record<string, string>) {
    setBusy(id);
    const res = await fetch(`/api/admin/orders/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    setBusy(null);
    if (!res.ok) return setError(data.error);
    setOrders((prev) => prev?.map((o) => (o.id === id ? data.order : o)) ?? null);
  }

  const visible = (orders ?? []).filter((o) => (status === "all" || o.status === status) && (!date || o.slot.date === date));
  const today = orders ? pktDate(new Date()) : "";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by status">
          {(["all", ...ORDER_STATUSES] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setStatus(s)}
              aria-pressed={status === s}
              className={`rounded-full px-4 py-1.5 text-sm ring-1 transition ${
                status === s ? "bg-ember-500 text-coal-950 ring-ember-500" : "text-ash-300 ring-coal-600 hover:ring-ash-400"
              }`}
            >
              {s === "all" ? "All" : STATUS_LABEL[s]}
              {orders && s !== "all" && <span className="ml-1.5 font-mono opacity-70">{orders.filter((o) => o.status === s).length}</span>}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-end gap-2">
          <div className="flex flex-col gap-1">
            <label htmlFor="date" className="text-xs text-ash-400">
              Delivery date
            </label>
            <input id="date" type="date" value={date} onChange={(e) => setDate(e.target.value)} className={field} />
          </div>
          {today && (
            <Button variant="ghost" className="h-10" onClick={() => setDate(date === today ? "" : today)}>
              {date === today ? "All dates" : "Today"}
            </Button>
          )}
          <Button variant="ghost" className="h-10" onClick={load} aria-label="Refresh orders">
            <ArrowClockwise size={16} />
          </Button>
        </div>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      {orders === null ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-28 animate-pulse rounded-[var(--radius-card)] bg-coal-900" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <p className="rounded-[var(--radius-card)] bg-coal-900 p-10 text-center text-ash-400">No orders match. New orders from the site appear here.</p>
      ) : (
        <ul className="space-y-3">
          {visible.map((o) => (
            <li key={o.id} className="rounded-[var(--radius-card)] bg-coal-900 p-5 ring-1 ring-white/[0.06]">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-ash-50">{o.id}</span>
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${statusStyle[o.status]}`}>{STATUS_LABEL[o.status]}</span>
                  </div>
                  <p className="mt-2 text-sm text-ash-100">
                    {o.customer.name}, {o.customer.phone}
                  </p>
                  <p className="mt-1 max-w-xl text-sm text-ash-400">
                    {o.customer.address}, {o.customer.area}
                    {o.customer.landmark ? ` (${/^near\b/i.test(o.customer.landmark) ? "" : "near "}${o.customer.landmark})` : ""}
                    {o.customer.notes ? `. Note: ${o.customer.notes}` : ""}
                  </p>
                  <p className="mt-1 text-sm font-medium text-ember-300">Deliver {describeSlot(o.slot)}</p>
                  <ul className="mt-1 text-sm text-ash-300">
                    {o.lines.map((l) => (
                      <li key={`${l.slug}-${l.chutney}-${l.salad}`}>
                        {l.qty} × {l.name}
                        {l.chutney ? ` (${l.chutney}, ${l.salad} salad)` : ""}
                      </li>
                    ))}
                  </ul>
                  {o.source?.utmSource && <p className="mt-1 text-xs text-ash-400">Source: {o.source.utmSource}</p>}
                </div>
                <div className="text-right">
                  <p className="font-mono text-lg text-ember-400">{formatPKR(o.total)}</p>
                  {o.deliveryFee === null && <p className="text-xs text-ash-400">+ delivery</p>}
                  <p className="text-xs text-ash-400">{new Date(o.createdAt).toLocaleString("en-GB", { timeZone: "Asia/Karachi" })}</p>
                </div>
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2">
                <a
                  href={`https://wa.me/${toIntlPk(o.customer.phone)}?text=${encodeURIComponent(
                    `Assalam o Alaikum ${o.customer.name.split(" ")[0]}, this is Instant Grill about order ${o.id} (${formatPKR(o.total)}), delivery ${describeSlot(o.slot)}. Can you confirm?`,
                  )}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex h-10 items-center gap-2 rounded-full bg-[#25d366] px-4 text-sm font-medium text-coal-950"
                >
                  <WhatsappLogo size={16} weight="fill" /> Message
                </a>
                <Link href={`/admin/orders/${o.id}/slip`} className="inline-flex h-10 items-center gap-2 rounded-full border border-coal-600 px-4 text-sm text-ash-100 hover:border-ash-400">
                  <Printer size={16} /> Slip
                </Link>
                {TRANSITIONS[o.status].map((next) => (
                  <Button
                    key={next}
                    variant={next === "cancelled" ? "ghost" : "primary"}
                    className="h-10"
                    disabled={busy === o.id}
                    onClick={() => {
                      if (next === "cancelled" && !confirm(`Cancel ${o.id}?`)) return;
                      patch(o.id, { status: next });
                    }}
                  >
                    {next === "cancelled" ? "Cancel" : `Mark ${STATUS_LABEL[next].toLowerCase()}`}
                  </Button>
                ))}
                {(o.status === "preparing" || o.status === "out_for_delivery") && (
                  <form
                    className="flex flex-wrap gap-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      patch(o.id, { rider: String(new FormData(e.currentTarget).get("rider")) });
                    }}
                  >
                    <input name="rider" aria-label="Rider" placeholder="Rider name" defaultValue={o.rider} className={`${field} w-40`} />
                    <Button type="submit" variant="ghost" className="h-10" disabled={busy === o.id}>
                      Save
                    </Button>
                  </form>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
