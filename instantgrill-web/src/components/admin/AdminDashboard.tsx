"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowClockwise, SignOut, WhatsappLogo } from "@phosphor-icons/react";
import { formatPKR } from "@/lib/pricing";
import { ORDER_STATUSES, TRANSITIONS, type Order, type OrderStatus } from "@/lib/store/types";
import { toIntlPk } from "@/lib/validation";
import { Button } from "../ui";

const TOKEN_KEY = "ig.admin";

const statusStyle: Record<OrderStatus, string> = {
  pending: "bg-ember-500/15 text-ember-300",
  confirmed: "bg-sky-400/15 text-sky-300",
  shipped: "bg-violet-400/15 text-violet-300",
  delivered: "bg-ok/15 text-ok",
  cancelled: "bg-coal-700 text-ash-400",
  returned: "bg-danger/15 text-danger",
};

const field =
  "h-10 rounded-[var(--radius-field)] border border-coal-600 bg-coal-850 px-3 text-sm text-ash-50 placeholder:text-ash-400 focus:border-ember-500 focus:outline-none";

export function AdminDashboard() {
  const [token, setToken] = useState<string | null>(null);
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [filter, setFilter] = useState<OrderStatus | "all">("all");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- browser-only storage read after mount
      setToken(sessionStorage.getItem(TOKEN_KEY));
    } catch {
      /* no storage: sign in each visit */
    }
  }, []);

  const load = useCallback(async (t: string) => {
    setError(null);
    const res = await fetch("/api/admin/orders", { headers: { authorization: `Bearer ${t}` } });
    if (res.status === 401) {
      setToken(null);
      try {
        sessionStorage.removeItem(TOKEN_KEY);
      } catch {}
      setError("Wrong token, or ADMIN_TOKEN is not set on the server.");
      return;
    }
    const data = await res.json();
    setOrders(data.orders);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on sign-in
    if (token) load(token);
  }, [token, load]);

  async function patch(id: string, body: Record<string, string>) {
    if (!token) return;
    setBusy(id);
    const res = await fetch(`/api/admin/orders/${id}`, {
      method: "PATCH",
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    setBusy(null);
    if (!res.ok) return setError(data.error);
    setOrders((prev) => prev?.map((o) => (o.id === id ? data.order : o)) ?? null);
  }

  const stats = useMemo(() => {
    const list = orders ?? [];
    const count = (s: OrderStatus) => list.filter((o) => o.status === s).length;
    const closed = count("delivered") + count("returned");
    return {
      pending: count("pending"),
      inTransit: count("confirmed") + count("shipped"),
      delivered: list.filter((o) => o.status === "delivered").reduce((s, o) => s + o.total, 0),
      rto: closed ? Math.round((count("returned") / closed) * 100) : null,
    };
  }, [orders]);

  if (!token) {
    return (
      <form
        className="mx-auto mt-10 max-w-sm space-y-4 rounded-[var(--radius-card)] bg-coal-900 p-8 ring-1 ring-white/[0.06]"
        onSubmit={(e) => {
          e.preventDefault();
          const t = String(new FormData(e.currentTarget).get("token") ?? "");
          try {
            sessionStorage.setItem(TOKEN_KEY, t);
          } catch {}
          setToken(t);
        }}
      >
        <h1 className="text-xl font-semibold text-ash-50">Orders admin</h1>
        <div className="flex flex-col gap-2">
          <label htmlFor="token" className="text-sm text-ash-100">
            Admin token
          </label>
          <input id="token" name="token" type="password" required className={`${field} h-12`} />
        </div>
        {error && <p className="text-sm text-danger">{error}</p>}
        <Button type="submit" className="w-full">
          Sign in
        </Button>
      </form>
    );
  }

  const visible = (orders ?? []).filter((o) => filter === "all" || o.status === filter);

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-3xl font-semibold tracking-[-0.03em] text-ash-50">Orders</h1>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => load(token)}>
            <ArrowClockwise size={16} /> Refresh
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              try {
                sessionStorage.removeItem(TOKEN_KEY);
              } catch {}
              setToken(null);
              setOrders(null);
            }}
          >
            <SignOut size={16} /> Sign out
          </Button>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          ["To confirm", String(stats.pending)],
          ["In transit", String(stats.inTransit)],
          ["Delivered revenue", formatPKR(stats.delivered)],
          ["Return rate", stats.rto === null ? "No data" : `${stats.rto}%`],
        ].map(([k, v]) => (
          <div key={k} className="rounded-[var(--radius-card)] bg-coal-900 p-5 ring-1 ring-white/[0.06]">
            <dt className="text-sm text-ash-400">{k}</dt>
            <dd className="mt-2 font-mono text-2xl text-ash-50">{v}</dd>
          </div>
        ))}
      </dl>

      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by status">
        {(["all", ...ORDER_STATUSES] as const).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setFilter(s)}
            aria-pressed={filter === s}
            className={`rounded-full px-4 py-1.5 text-sm capitalize ring-1 transition ${
              filter === s ? "bg-ember-500 text-coal-950 ring-ember-500" : "text-ash-300 ring-coal-600 hover:ring-ash-400"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      {orders === null ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-28 animate-pulse rounded-[var(--radius-card)] bg-coal-900" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <p className="rounded-[var(--radius-card)] bg-coal-900 p-10 text-center text-ash-400">
          No {filter === "all" ? "" : filter} orders yet. New orders from the site appear here.
        </p>
      ) : (
        <ul className="space-y-3">
          {visible.map((o) => (
            <li key={o.id} className="rounded-[var(--radius-card)] bg-coal-900 p-5 ring-1 ring-white/[0.06]">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-ash-50">{o.id}</span>
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ${statusStyle[o.status]}`}>
                      {o.status}
                    </span>
                  </div>
                  <p className="mt-2 text-sm text-ash-100">
                    {o.customer.name}, {o.customer.phone}
                  </p>
                  <p className="mt-1 max-w-xl text-sm text-ash-400">
                    {o.customer.address}, {o.customer.city}
                    {o.customer.notes ? `. Note: ${o.customer.notes}` : ""}
                  </p>
                  <p className="mt-1 text-sm text-ash-400">
                    {o.lines.map((l) => `${l.name} × ${l.qty}`).join(", ")}
                    {o.source?.utmSource ? `. Source: ${o.source.utmSource}` : ""}
                  </p>
                </div>
                <div className="text-right">
                  <p className="font-mono text-lg text-ember-400">{formatPKR(o.total)}</p>
                  <p className="text-xs text-ash-400">{new Date(o.createdAt).toLocaleString("en-PK")}</p>
                </div>
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2">
                <a
                  href={`https://wa.me/${toIntlPk(o.customer.phone)}?text=${encodeURIComponent(
                    `Assalam o Alaikum ${o.customer.name.split(" ")[0]}, this is Instant Grill about order ${o.id} (${formatPKR(o.total)}). Can you confirm your address?`,
                  )}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex h-10 items-center gap-2 rounded-full bg-[#25d366] px-4 text-sm font-medium text-coal-950"
                >
                  <WhatsappLogo size={16} weight="fill" /> Message
                </a>
                {TRANSITIONS[o.status].map((next) => (
                  <Button
                    key={next}
                    variant={next === "cancelled" || next === "returned" ? "ghost" : "primary"}
                    className="h-10 capitalize"
                    disabled={busy === o.id}
                    onClick={() => patch(o.id, { status: next })}
                  >
                    Mark {next}
                  </Button>
                ))}
                {(o.status === "confirmed" || o.status === "shipped") && (
                  <form
                    className="flex flex-wrap gap-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const fd = new FormData(e.currentTarget);
                      patch(o.id, { courier: String(fd.get("courier")), trackingNumber: String(fd.get("tn")) });
                    }}
                  >
                    <input name="courier" aria-label="Courier" placeholder="Courier" defaultValue={o.courier} className={`${field} w-32`} />
                    <input name="tn" aria-label="Tracking number" placeholder="Tracking no." defaultValue={o.trackingNumber} className={`${field} w-40`} />
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
