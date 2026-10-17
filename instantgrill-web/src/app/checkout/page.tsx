import type { Metadata } from "next";
import { CheckoutForm } from "@/components/order/CheckoutForm";
import { Container } from "@/components/ui";

export const metadata: Metadata = { title: "Checkout", robots: { index: false } };

export default function CheckoutPage() {
  return (
    <Container className="py-14 md:py-20">
      <h1 className="mb-10 text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">Checkout</h1>
      <CheckoutForm />
    </Container>
  );
}
