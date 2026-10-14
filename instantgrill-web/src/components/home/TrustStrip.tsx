import { ArrowsClockwise, ChatCircleText, Money, Truck } from "@phosphor-icons/react/dist/ssr";
import { shipping } from "@/lib/catalog";
import { Container } from "../ui";

const items = [
  { icon: Money, title: "Cash on delivery", body: "Pay when it reaches you" },
  { icon: Truck, title: "Delivery nationwide", body: shipping.eta },
  { icon: ArrowsClockwise, title: `${shipping.replacementDays}-day replacement`, body: "Damaged in transit? We replace it" },
  { icon: ChatCircleText, title: "Real support", body: "A person answers on WhatsApp" },
];

export function TrustStrip() {
  return (
    <section aria-label="Our promises" className="border-y border-white/[0.06] bg-coal-900">
      <Container className="grid grid-cols-2 gap-x-6 gap-y-8 py-10 lg:grid-cols-4">
        {items.map(({ icon: Icon, title, body }) => (
          <div key={title} className="flex items-start gap-3">
            <Icon size={26} className="mt-0.5 shrink-0 text-ember-400" />
            <div>
              <p className="text-sm font-medium text-ash-50">{title}</p>
              <p className="mt-1 text-sm leading-snug text-ash-400">{body}</p>
            </div>
          </div>
        ))}
      </Container>
    </section>
  );
}
