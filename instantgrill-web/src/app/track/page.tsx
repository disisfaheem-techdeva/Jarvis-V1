import type { Metadata } from "next";
import { TrackForm } from "@/components/order/OrderView";
import { Container } from "@/components/ui";

export const metadata: Metadata = {
  title: "Track your order",
  description: "Check the status of your Instant Grill order with your order number and mobile number.",
};

export default function TrackPage() {
  return (
    <Container className="max-w-3xl py-14 md:py-20">
      <h1 className="mb-8 text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">Track your order</h1>
      <TrackForm />
    </Container>
  );
}
