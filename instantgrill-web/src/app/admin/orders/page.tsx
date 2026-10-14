import { AdminPage } from "@/components/admin/gate";
import { OrdersBoard } from "@/components/admin/OrdersBoard";
import { ORDER_STATUSES, type OrderStatus } from "@/lib/store/types";

export default async function OrdersPage({ searchParams }: { searchParams: Promise<{ status?: string }> }) {
  const { status } = await searchParams;
  const initial = ORDER_STATUSES.includes(status as OrderStatus) ? (status as OrderStatus) : undefined;
  return <AdminPage title="Orders">{() => <OrdersBoard initialStatus={initial} />}</AdminPage>;
}
