import { AdminPage } from "@/components/admin/gate";
import { customersFrom } from "@/lib/admin-stats";
import { formatPKR } from "@/lib/pricing";
import { store } from "@/lib/store";
import { toIntlPk } from "@/lib/validation";

export default function CustomersPage() {
  return (
    <AdminPage title="Customers">
      {async () => {
        const customers = customersFrom(await store().orders.list({ limit: 5000 }));
        if (customers.length === 0) {
          return <p className="rounded-[var(--radius-card)] bg-coal-900 p-10 text-center text-ash-400">Customers appear here after their first order.</p>;
        }
        return (
          <div className="overflow-x-auto rounded-[var(--radius-card)] bg-coal-900 ring-1 ring-white/[0.06]">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="text-ash-400">
                <tr>
                  {["Name", "Phone", "Area", "Orders", "Spent", "Last order"].map((h) => (
                    <th key={h} scope="col" className="px-5 py-4 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {customers.map((c) => (
                  <tr key={c.phone}>
                    <td className="px-5 py-4 text-ash-50">{c.name}</td>
                    <td className="px-5 py-4">
                      <a className="font-mono text-ash-100 hover:text-ember-300" href={`https://wa.me/${toIntlPk(c.phone)}`} target="_blank" rel="noopener noreferrer">
                        {c.phone}
                      </a>
                    </td>
                    <td className="px-5 py-4 text-ash-300">{c.area}</td>
                    <td className="px-5 py-4 font-mono text-ash-100">{c.orders}</td>
                    <td className="px-5 py-4 font-mono text-ash-100">{formatPKR(c.spent)}</td>
                    <td className="px-5 py-4 text-ash-300">{new Date(c.lastOrderAt).toLocaleDateString("en-GB", { timeZone: "Asia/Karachi" })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }}
    </AdminPage>
  );
}
