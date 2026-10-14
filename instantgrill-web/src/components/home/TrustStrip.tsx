import { ChatCircleText, Clock, Money, UsersThree } from "@phosphor-icons/react/dist/ssr";
import { Container } from "../ui";

const items = [
  { icon: Money, title: "Cash on delivery", body: "Pay the rider when it arrives" },
  { icon: Clock, title: "Within 6 hours", body: "Or next morning, 10 AM to 12 PM" },
  { icon: UsersThree, title: "Serves 2", body: "About 850 g meat, everything included" },
  { icon: ChatCircleText, title: "Order on WhatsApp", body: "A person answers, 12 PM to 12 AM" },
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
