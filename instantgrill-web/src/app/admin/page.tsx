import Link from "next/link";
import { AdminPage } from "@/components/admin/gate";
import { dashboardStats } from "@/lib/admin-stats";
import { formatPKR } from "@/lib/pricing";
import { ORDER_STATUSES, STATUS_LABEL, store } from "@/lib/store";

export default function AdminHome() {
  return (
    <AdminPage title="Dashboard">
      {async () => {
        const [orders, enquiries] = await Promise.all([store().orders.list(), store().enquiries.list(5)]);
        const s = dashboardStats(orders);
        const tiles = [
          ["Orders today", String(s.ordersToday)],
          ["Revenue today", formatPKR(s.revenueToday)],
          ["Revenue, last 7 days", formatPKR(s.revenueWeek)],
          ["Due for delivery today", String(s.dueToday)],
        ];
        return (
          <div className="space-y-8">
            <dl className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              {tiles.map(([k, v]) => (
                <div key={k} className="rounded-[var(--radius-card)] bg-coal-900 p-5 ring-1 ring-white/[0.06]">
                  <dt className="text-sm text-ash-400">{k}</dt>
                  <dd className="mt-2 font-mono text-2xl text-ash-50">{v}</dd>
                </div>
              ))}
            </dl>
            <p className="text-xs text-ash-400">Revenue counts every order placed that is not cancelled, before delivery fees still to confirm.</p>

            <div className="grid gap-4 lg:grid-cols-2">
              <section className="rounded-[var(--radius-card)] bg-coal-900 p-6 ring-1 ring-white/[0.06]">
                <h2 className="text-lg font-semibold text-ash-50">Orders by status</h2>
                <ul className="mt-4 space-y-2">
                  {ORDER_STATUSES.map((st) => (
                    <li key={st}>
                      <Link href={`/admin/orders?status=${st}`} className="flex justify-between rounded-[var(--radius-field)] px-3 py-2 text-sm hover:bg-coal-850">
                        <span className="text-ash-100">{STATUS_LABEL[st]}</span>
                        <span className="font-mono text-ash-50">{s.byStatus[st] ?? 0}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
              <section className="rounded-[var(--radius-card)] bg-coal-900 p-6 ring-1 ring-white/[0.06]">
                <h2 className="text-lg font-semibold text-ash-50">Top items, last 7 days</h2>
                {s.topItems.length === 0 ? (
                  <p className="mt-4 text-sm text-ash-400">No orders this week yet.</p>
                ) : (
                  <ol className="mt-4 space-y-2">
                    {s.topItems.map((t, i) => (
                      <li key={t.name} className="flex justify-between px-3 py-2 text-sm">
                        <span className="text-ash-100">
                          <span className="mr-2 font-mono text-ember-400">{i + 1}</span>
                          {t.name}
                        </span>
                        <span className="font-mono text-ash-300">
                          {t.qty} sold, {formatPKR(t.revenue)}
                        </span>
                      </li>
                    ))}
                  </ol>
                )}
              </section>
            </div>

            <section className="rounded-[var(--radius-card)] bg-coal-900 p-6 ring-1 ring-white/[0.06]">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-ash-50">Latest enquiries</h2>
                <Link href="/admin/enquiries" className="text-sm text-ember-300 hover:underline">
                  See all
                </Link>
              </div>
              {enquiries.length === 0 ? (
                <p className="mt-4 text-sm text-ash-400">No event or bulk enquiries yet.</p>
              ) : (
                <ul className="mt-4 divide-y divide-white/[0.06]">
                  {enquiries.map((e) => (
                    <li key={e.id} className="py-3 text-sm text-ash-300">
                      <span className="text-ash-50">{e.name}</span>, {e.people ? `${e.people} people` : "people TBC"}
                      {e.date ? `, ${e.date}` : ""} ({e.phone})
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        );
      }}
    </AdminPage>
  );
}
