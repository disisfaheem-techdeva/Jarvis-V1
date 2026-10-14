"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { MagnifyingGlass, X } from "@phosphor-icons/react";
import { CATEGORIES, products, type Category } from "@/lib/catalog";
import { formatPKR } from "@/lib/pricing";
import { useCart } from "../cart/CartProvider";
import { Container, buttonClass } from "../ui";
import { ProductCard } from "./ProductCard";

const FILTERS = [
  { id: "boneless", label: "Boneless" },
  { id: "kabab", label: "Kabab" },
  { id: "premium", label: "Premium" },
] as const;
type Filter = (typeof FILTERS)[number]["id"];

export function MenuBrowser() {
  const [active, setActive] = useState<Category>("chicken");
  const [filters, setFilters] = useState<Filter[]>([]);
  const [query, setQuery] = useState("");
  const tabsRef = useRef<HTMLDivElement>(null);
  const cart = useCart();

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return products.filter(
      (p) =>
        (!q || p.name.toLowerCase().includes(q)) &&
        filters.every((f) => p.tags?.includes(f)),
    );
  }, [filters, query]);

  const sections = CATEGORIES.map((c) => ({ ...c, items: visible.filter((p) => p.category === c.id) })).filter(
    (s) => s.items.length > 0,
  );

  // Highlight the tab of the section currently under the sticky bar.
  useEffect(() => {
    const els = CATEGORIES.map((c) => document.getElementById(`cat-${c.id}`)).filter(Boolean) as HTMLElement[];
    const io = new IntersectionObserver(
      (entries) => {
        const top = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (top) setActive(top.target.id.replace("cat-", "") as Category);
      },
      { rootMargin: "-140px 0px -55% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [sections.length]);

  // Keep the active tab scrolled into view on narrow screens.
  useEffect(() => {
    tabsRef.current?.querySelector<HTMLElement>(`[data-tab="${active}"]`)?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [active]);

  return (
    <>
      <div className="sticky top-16 z-20 border-b border-white/[0.06] bg-coal-950/85 backdrop-blur-xl">
        <Container>
          <div ref={tabsRef} className="-mx-4 flex gap-1 overflow-x-auto px-4 py-3 [scrollbar-width:none] sm:mx-0 sm:px-0">
            {sections.map((s) => (
              <a
                key={s.id}
                data-tab={s.id}
                href={`#cat-${s.id}`}
                aria-current={active === s.id ? "true" : undefined}
                className={`shrink-0 rounded-full px-4 py-2 text-sm font-medium transition ${
                  active === s.id ? "bg-ember-500 text-coal-950" : "text-ash-300 hover:text-ash-50"
                }`}
              >
                {s.label}
              </a>
            ))}
          </div>
        </Container>
      </div>

      <Container className="py-10">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap gap-2" role="group" aria-label="Filters">
            {FILTERS.map((f) => {
              const on = filters.includes(f.id);
              return (
                <button
                  key={f.id}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setFilters((cur) => (on ? cur.filter((x) => x !== f.id) : [...cur, f.id]))}
                  className={`rounded-full px-4 py-2 text-sm ring-1 transition ${
                    on ? "bg-ash-50 text-coal-950 ring-ash-50" : "text-ash-300 ring-coal-600 hover:ring-ash-400"
                  }`}
                >
                  {f.label}
                </button>
              );
            })}
          </div>
          <div className="relative sm:w-72">
            <label htmlFor="menu-search" className="sr-only">
              Search the menu
            </label>
            <MagnifyingGlass size={18} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ash-400" />
            <input
              id="menu-search"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search tikka, seekh, malai..."
              className="h-11 w-full rounded-full border border-coal-600 bg-coal-850 pl-11 pr-4 text-sm text-ash-50 placeholder:text-ash-400 focus:border-ember-500 focus:outline-none"
            />
          </div>
        </div>

        {sections.length === 0 ? (
          <div className="mt-16 rounded-[var(--radius-card)] bg-coal-900 p-10 text-center">
            <p className="text-ash-300">Nothing matches that. Try another word or clear the filters.</p>
            <button
              type="button"
              className={buttonClass("ghost", "md", "mt-6")}
              onClick={() => {
                setQuery("");
                setFilters([]);
              }}
            >
              <X size={16} /> Clear
            </button>
          </div>
        ) : (
          sections.map((s, si) => (
            <section key={s.id} id={`cat-${s.id}`} className="scroll-mt-36 pt-14">
              <h2 className="text-3xl font-semibold tracking-[-0.03em] text-ash-50">{s.label}</h2>
              <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {s.items.map((p, i) => (
                  <ProductCard key={p.slug} product={p} priority={si === 0 && i < 2} />
                ))}
              </div>
            </section>
          ))
        )}
      </Container>

      {cart.hydrated && cart.count > 0 && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-white/[0.08] bg-coal-900/90 px-4 pb-[calc(env(safe-area-inset-bottom)+12px)] pt-3 backdrop-blur-xl md:hidden">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-xs text-ash-400">{cart.count} item{cart.count === 1 ? "" : "s"}</p>
              <p className="font-mono font-semibold text-ash-50">{formatPKR(cart.subtotal)}</p>
            </div>
            <Link href="/checkout" className={buttonClass("primary", "md", "flex-1")}>
              Checkout
            </Link>
          </div>
        </div>
      )}
    </>
  );
}
