"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, Plus } from "@phosphor-icons/react";
import { CHUTNEYS, SALADS, isBox, type Chutney, type Product, type Salad } from "@/lib/catalog";
import { savingIn } from "@/lib/menu-types";
import { formatPKR } from "@/lib/pricing";
import { useCart } from "../cart/CartProvider";
import { MotionImage } from "../MotionImage";
import { Button } from "../ui";
import { ChoicePills } from "./ChoicePills";
import { useMenu } from "./MenuProvider";

export function productMeta(p: Product): string {
  const parts: string[] = [];
  if (p.pieces) parts.push(`${p.pieces} pc`);
  if (p.serves) parts.push(`Serves ${p.serves}`);
  if (p.category !== "extra" && p.category !== "drink" && p.serves === 2) parts.push("~850 g");
  return parts.join(" · ");
}

/**
 * Menu card. Extras and drinks add in one tap. Boxes need a chutney and salad
 * choice, so "Add" opens the two choices inline instead of guessing for the customer.
 */
export function ProductCard({ product: p, priority = false }: { product: Product; priority?: boolean }) {
  const cart = useCart();
  const box = isBox(p);
  const [picking, setPicking] = useState(false);
  const [chutney, setChutney] = useState<Chutney>();
  const [salad, setSalad] = useState<Salad>();
  const [added, setAdded] = useState(false);
  const saving = savingIn(useMenu(), p);

  function add(line: Parameters<typeof cart.add>[0]) {
    cart.add(line, { open: false });
    setAdded(true);
    setPicking(false);
    setTimeout(() => setAdded(false), 1600);
  }

  return (
    <article
      className={`group flex flex-col overflow-hidden rounded-[var(--radius-card)] bg-coal-900 ring-1 ring-white/[0.06] ${
        p.soldOut ? "opacity-50 grayscale" : ""
      }`}
    >
      <Link href={`/menu/${p.slug}`} className="relative block" aria-label={`${p.name} details`}>
        <MotionImage
          slot={{ ...p.image, brief: "" }}
          priority={priority}
          parallax={0}
          sizes="(min-width: 1024px) 25vw, (min-width: 640px) 50vw, 100vw"
          className="aspect-[4/3] transition-transform duration-700 ease-[var(--ease-out-soft)] group-hover:scale-[1.04]"
        />
        {p.soldOut && (
          <span className="absolute left-3 top-3 rounded-full bg-coal-950/80 px-3 py-1 text-xs font-medium text-ash-100">
            Sold out
          </span>
        )}
      </Link>
      <div className="flex flex-1 flex-col p-5">
        <h3 className="text-lg font-semibold tracking-[-0.01em] text-ash-50">
          <Link href={`/menu/${p.slug}`} className="hover:text-ember-300">
            {p.name}
          </Link>
        </h3>
        {productMeta(p) && <p className="mt-1 text-sm text-ash-400">{productMeta(p)}</p>}
        <div className="mt-auto flex items-end justify-between gap-3 pt-5">
          <div>
            <p className="font-mono text-lg font-semibold text-ember-400">{formatPKR(p.price)}</p>
            {saving && <p className="text-xs text-ember-300">Save {formatPKR(saving)}</p>}
          </div>
          {!p.soldOut && !picking && (
            <Button
              className="h-10 px-4"
              aria-label={`Add ${p.name}`}
              onClick={() => (box ? setPicking(true) : add({ slug: p.slug, qty: 1 }))}
            >
              {added ? <Check size={16} weight="bold" /> : <Plus size={16} weight="bold" />}
              {added ? "Added" : "Add"}
            </Button>
          )}
        </div>
        {picking && (
          <div className="mt-4 space-y-4 border-t border-white/[0.06] pt-4">
            <ChoicePills name={`${p.slug}-chutney`} label="Chutney" options={CHUTNEYS} value={chutney} onChange={setChutney} />
            <ChoicePills name={`${p.slug}-salad`} label="Salad" options={SALADS} value={salad} onChange={setSalad} />
            <div className="flex gap-2">
              <Button
                className="h-10 flex-1"
                disabled={!chutney || !salad}
                onClick={() => add({ slug: p.slug, qty: 1, chutney, salad })}
              >
                Add to cart
              </Button>
              <Button variant="ghost" className="h-10" onClick={() => setPicking(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>
    </article>
  );
}
