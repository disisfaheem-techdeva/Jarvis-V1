import Link from "next/link";
import { Fire, InstagramLogo, TiktokLogo, FacebookLogo } from "@phosphor-icons/react/dist/ssr";
import { brand } from "@/lib/catalog";
import { Container } from "./ui";
import { whatsappLink } from "./WhatsAppFab";

export function Footer() {
  return (
    <footer className="border-t border-white/[0.06] bg-coal-950 pb-28 pt-16 md:pb-12">
      <Container className="grid gap-12 md:grid-cols-[1.4fr_1fr_1.2fr]">
        <div>
          <Link href="/" className="flex items-center gap-2 font-semibold text-ash-50">
            <span className="grid size-8 place-items-center rounded-full bg-ember-500 text-coal-950">
              <Fire size={18} weight="fill" />
            </span>
            {brand.name}
          </Link>
          <p className="mt-4 max-w-xs text-sm leading-relaxed text-ash-400">{brand.positioning}.</p>
          <p className="mt-2 max-w-xs text-sm leading-relaxed text-ash-400">{brand.hoursDisplay}</p>
          <div className="mt-6 flex gap-2">
            {[
              { href: brand.instagram, label: "Instagram", Icon: InstagramLogo },
              { href: brand.tiktok, label: "TikTok", Icon: TiktokLogo },
              { href: brand.facebook, label: "Facebook", Icon: FacebookLogo },
            ].map(({ href, label, Icon }) => (
              <a
                key={label}
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={label}
                className="grid size-10 place-items-center rounded-full border border-coal-600 text-ash-300 hover:border-ash-400 hover:text-ash-50"
              >
                <Icon size={18} />
              </a>
            ))}
          </div>
        </div>
        <div>
          <h2 className="text-sm font-medium text-ash-50">Order</h2>
          <ul className="mt-4 space-y-3 text-sm text-ash-400">
            <li><Link className="hover:text-ash-50" href="/menu">Menu</Link></li>
            <li><Link className="hover:text-ash-50" href="/#deals">Family and party deals</Link></li>
            <li><Link className="hover:text-ash-50" href="/track">Track your order</Link></li>
            <li><Link className="hover:text-ash-50" href="/policies">Delivery and orders</Link></li>
          </ul>
        </div>
        <div>
          <h2 className="text-sm font-medium text-ash-50">Contact</h2>
          <ul className="mt-4 space-y-3 text-sm text-ash-400">
            <li>
              <a className="hover:text-ash-50" href={whatsappLink("Assalam o Alaikum Instant Grill")} target="_blank" rel="noopener noreferrer">
                WhatsApp {brand.phoneDisplay}
              </a>
            </li>
            <li><a className="hover:text-ash-50" href={`mailto:${brand.email}`}>{brand.email}</a></li>
            <li>{brand.address}</li>
          </ul>
        </div>
      </Container>
      <Container className="mt-14 text-xs text-ash-400">
        © {new Date().getFullYear()} {brand.name}. All rights reserved.
      </Container>
    </footer>
  );
}
