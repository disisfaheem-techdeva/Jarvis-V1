import { AdminPage } from "@/components/admin/gate";
import { store } from "@/lib/store";
import { toIntlPk } from "@/lib/validation";

const when = (iso: string) => new Date(iso).toLocaleString("en-GB", { timeZone: "Asia/Karachi", dateStyle: "medium", timeStyle: "short" });

export default function EnquiriesPage() {
  return (
    <AdminPage title="Enquiries">
      {async () => {
        const [enquiries, messages] = await Promise.all([store().enquiries.list(), store().messages.list()]);
        return (
          <div className="grid gap-8 lg:grid-cols-2">
            <section>
              <h2 className="text-lg font-semibold text-ash-50">Bulk and event enquiries</h2>
              {enquiries.length === 0 ? (
                <p className="mt-4 rounded-[var(--radius-card)] bg-coal-900 p-8 text-sm text-ash-400">None yet. They come from the form on the Deals page.</p>
              ) : (
                <ul className="mt-4 space-y-3">
                  {enquiries.map((e) => (
                    <li key={e.id} className="rounded-[var(--radius-card)] bg-coal-900 p-5 ring-1 ring-white/[0.06]">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <p className="font-medium text-ash-50">
                          {e.name} <span className="ml-1 rounded-full bg-ember-500/15 px-2 py-0.5 text-xs capitalize text-ember-300">{e.type}</span>
                        </p>
                        <span className="text-xs text-ash-400">{when(e.createdAt)}</span>
                      </div>
                      <p className="mt-2 text-sm text-ash-300">
                        {[e.people && `${e.people} people`, e.date && `on ${e.date}`, e.area].filter(Boolean).join(", ") || "No details given"}
                      </p>
                      {e.message && <p className="mt-2 text-sm text-ash-100">{e.message}</p>}
                      <a className="mt-3 inline-block text-sm text-ember-300 hover:underline" href={`https://wa.me/${toIntlPk(e.phone)}`} target="_blank" rel="noopener noreferrer">
                        WhatsApp {e.phone}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section>
              <h2 className="text-lg font-semibold text-ash-50">Contact messages</h2>
              {messages.length === 0 ? (
                <p className="mt-4 rounded-[var(--radius-card)] bg-coal-900 p-8 text-sm text-ash-400">None yet. They come from the Contact page.</p>
              ) : (
                <ul className="mt-4 space-y-3">
                  {messages.map((m) => (
                    <li key={m.id} className="rounded-[var(--radius-card)] bg-coal-900 p-5 ring-1 ring-white/[0.06]">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <p className="font-medium text-ash-50">{m.name}</p>
                        <span className="text-xs text-ash-400">{when(m.createdAt)}</span>
                      </div>
                      <p className="mt-2 whitespace-pre-line text-sm text-ash-100">{m.message}</p>
                      <p className="mt-3 flex flex-wrap gap-4 text-sm">
                        {m.phone && (
                          <a className="text-ember-300 hover:underline" href={`https://wa.me/${toIntlPk(m.phone)}`} target="_blank" rel="noopener noreferrer">
                            WhatsApp {m.phone}
                          </a>
                        )}
                        {m.email && (
                          <a className="text-ember-300 hover:underline" href={`mailto:${m.email}`}>
                            {m.email}
                          </a>
                        )}
                      </p>
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
