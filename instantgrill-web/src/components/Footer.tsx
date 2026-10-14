import Link from "next/link";
import { Fire } from "@phosphor-icons/react/dist/ssr";
import { brand } from "@/lib/catalog";
import { Container } from "./ui";
import { whatsappLink } from "./WhatsAppFab";

export function Footer() {
  return (
    <footer className="border-t border-white/[0.06] bg-coal-950 pb-28 pt-16 md:pb-12">
      <Container className="grid gap-12 md:grid-cols-[1.5fr_1fr_1fr]">
        <div>
          <Link href="/" className="flex items-center gap-2 font-semibold text-ash-50">
            <span className="grid size-8 place-items-center rounded-full bg-ember-500 text-coal-950">
              <Fire size={18} weight="fill" />
            </span>
            {brand.name}
          </Link>
          <p className="mt-4 max-w-xs text-sm leading-relaxed text-ash-400">{brand.tagline}</p>
        </div>
        <div>
          <h2 className="text-sm font-medium text-ash-50">Shop</h2>
          <ul className="mt-4 space-y-3 text-sm text-ash-400">
            <li><Link className="hover:text-ash-50" href="/#shop">Kits and prices</Link></li>
            <li><Link className="hover:text-ash-50" href="/track">Track your order</Link></li>
            <li><Link className="hover:text-ash-50" href="/policies">Delivery and returns</Link></li>
          </ul>
        </div>
        <div>
          <h2 className="text-sm font-medium text-ash-50">Contact</h2>
          <ul className="mt-4 space-y-3 text-sm text-ash-400">
            <li>
              <a className="hover:text-ash-50" href={whatsappLink("Hi Instant Grill")} target="_blank" rel="noopener noreferrer">
                WhatsApp us
              </a>
            </li>
            <li><a className="hover:text-ash-50" href={`mailto:${brand.email}`}>{brand.email}</a></li>
          </ul>
        </div>
      </Container>
      <Container className="mt-14 text-xs text-ash-400">
        © {new Date().getFullYear()} {brand.name}. All rights reserved.
      </Container>
    </footer>
  );
}
