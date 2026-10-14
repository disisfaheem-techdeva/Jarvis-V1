import type { Metadata } from "next";
import { OrderConfirmation } from "@/components/order/OrderView";
import { Container } from "@/components/ui";

export const metadata: Metadata = { title: "Your order", robots: { index: false } };

export default async function OrderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Container className="max-w-3xl py-14 md:py-20">
      <OrderConfirmation id={decodeURIComponent(id).toUpperCase()} />
    </Container>
  );
}
