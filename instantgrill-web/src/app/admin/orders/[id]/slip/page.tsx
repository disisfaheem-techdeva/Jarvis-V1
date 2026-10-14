import { notFound } from "next/navigation";
import { AdminPage } from "@/components/admin/gate";
import { PrintButton } from "@/components/admin/PrintButton";
import { describeSlot } from "@/lib/delivery";
import { formatPKR } from "@/lib/pricing";
import { store } from "@/lib/store";

/** Printable kitchen slip: what to pack, for whom, by when. */
export default async function SlipPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <AdminPage title="Kitchen slip">
      {async () => {
        const o = await store().orders.get(id);
        if (!o) notFound();
        return (
          <div className="mx-auto max-w-md">
            <PrintButton />
            <article className="mt-6 rounded-[var(--radius-card)] bg-white p-8 font-mono text-sm text-black print:mt-0 print:rounded-none print:p-0">
              <p className="text-center text-lg font-bold">INSTANT GRILL</p>
              <p className="text-center">Kitchen slip</p>
              <hr className="my-4 border-black" />
              <p className="text-xl font-bold">{o.id}</p>
              <p>Deliver: {describeSlot(o.slot)}</p>
              <p>Placed: {new Date(o.createdAt).toLocaleString("en-GB", { timeZone: "Asia/Karachi" })}</p>
              <hr className="my-4 border-dashed border-black" />
              <ul className="space-y-3">
                {o.lines.map((l) => (
                  <li key={`${l.slug}-${l.chutney}-${l.salad}`}>
                    <p className="text-base font-bold">
                      {l.qty} × {l.name}
                    </p>
                    {l.chutney && (
                      <p>
                        Chutney: {l.chutney} | Salad: {l.salad}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
              <hr className="my-4 border-dashed border-black" />
              <p className="font-bold">{o.customer.name}</p>
              <p>{o.customer.phone}</p>
              <p>
                {o.customer.address}, {o.customer.area}
              </p>
              {o.customer.landmark && <p>Near: {o.customer.landmark}</p>}
              {o.customer.notes && <p>Note: {o.customer.notes}</p>}
              <hr className="my-4 border-dashed border-black" />
              <p className="text-base font-bold">
                COD: {formatPKR(o.total)}
                {o.deliveryFee === null ? " + delivery" : ""}
              </p>
              {o.rider && <p>Rider: {o.rider}</p>}
            </article>
          </div>
        );
      }}
    </AdminPage>
  );
}
