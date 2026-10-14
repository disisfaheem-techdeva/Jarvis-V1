"use client";

import Link from "next/link";
import { Fire, Handbag } from "@phosphor-icons/react";
import { useCart } from "./cart/CartProvider";
import { ButtonLink, Container } from "./ui";

const nav = [
  { href: "/menu", label: "Menu" },
  { href: "/deals", label: "Deals" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/track", label: "Track order" },
];

export function Header() {
  const { count, setOpen, hydrated } = useCart();
  return (
    <header className="sticky top-0 z-30 print:hidden border-b border-white/[0.06] bg-coal-950/75 backdrop-blur-xl">
      <Container className="flex h-16 items-center justify-between gap-6">
        <Link href="/" className="flex items-center gap-2 text-[15px] font-semibold tracking-tight text-ash-50">
          <span className="grid size-8 place-items-center rounded-full bg-ember-500 text-coal-950">
            <Fire size={18} weight="fill" />
          </span>
          Instant Grill
        </Link>

        <nav aria-label="Main" className="hidden items-center gap-7 text-sm text-ash-300 md:flex">
          {nav.map((n) => (
            <Link key={n.href} href={n.href} className="transition-colors hover:text-ash-50">
              {n.label}
            </Link>
          ))}
        </nav>

        <div className="flex items-center gap-2">
          <ButtonLink href="/menu" className="h-10 px-4">
            Order
          </ButtonLink>
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="relative grid size-11 place-items-center rounded-full border border-coal-600 text-ash-100 transition-colors hover:border-ash-400 active:scale-[0.97]"
            aria-label={`Open cart, ${count} item${count === 1 ? "" : "s"}`}
          >
            <Handbag size={20} />
            {hydrated && count > 0 && (
              <span className="absolute -right-1 -top-1 grid min-w-5 place-items-center rounded-full bg-ember-500 px-1 text-[11px] font-semibold text-coal-950">
                {count}
              </span>
            )}
          </button>
        </div>
      </Container>
    </header>
  );
}
