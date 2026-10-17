"use client";

import { useEffect, useState } from "react";
import { track } from "@/lib/analytics";
import { useRouter } from "next/navigation";
import { CheckCircle, Minus, Plus, WhatsappLogo } from "@phosphor-icons/react";
import { BOX_INCLUDES, CHUTNEYS, SALADS, isBox, type Chutney, type Product, type Salad } from "@/lib/catalog";
import { savingIn } from "@/lib/menu-types";
import { MAX_QTY_PER_LINE, formatPKR } from "@/lib/pricing";
import { useCart } from "../cart/CartProvider";
import { Button } from "../ui";
import { whatsappLink } from "@/lib/whatsapp";
import { ChoicePills } from "./ChoicePills";
import { useMenu } from "./MenuProvider";
import { productMeta } from "./ProductCard";

function Stepper({ value, onChange, label, min = 0 }: { value: number; onChange: (n: number) => void; label: string; min?: number }) {
  return (
    <div className="flex items-center rounded-full border border-coal-600">
      <button
        type="button"
        className="grid size-10 place-items-center text-ash-300 hover:text-ash-50 disabled:opacity-40"
        disabled={value <= min}
        onClick={() => onChange(Math.max(min, value - 1))}
        aria-label={`Fewer ${label}`}
      >
        <Minus size={14} />
      </button>
      <span className="w-7 text-center font-mono text-sm" aria-live="polite" aria-label={`${label}: ${value}`}>
        {value}
      </span>
      <button
        type="button"
        className="grid size-10 place-items-center text-ash-300 hover:text-ash-50 disabled:opacity-40"
        disabled={value >= MAX_QTY_PER_LINE}
        onClick={() => onChange(Math.min(MAX_QTY_PER_LINE, value + 1))}
        aria-label={`More ${label}`}
      >
        <Plus size={14} />
      </button>
    </div>
  );
}

export function BoxBuilder({ product: p }: { product: Product }) {
  const cart = useCart();
  const menu = useMenu();
  const ADDONS = menu.products.filter((a) => (a.category === "extra" || a.category === "drink") && !a.soldOut);
  const router = useRouter();
  const box = isBox(p);
  const [chutney, setChutney] = useState<Chutney>();
  const [salad, setSalad] = useState<Salad>();
  const [qty, setQty] = useState(1);
  const [addons, setAddons] = useState<Record<string, number>>({});
  const [tried, setTried] = useState(false);
  const saving = savingIn(menu, p);

  useEffect(() => {
    track("ViewContent", { value: p.price, items: [{ id: p.slug, name: p.name, price: p.price, qty: 1 }] });
  }, [p.slug, p.name, p.price]);

  const addonTotal = ADDONS.reduce((s, a) => s + a.price * (addons[a.slug] ?? 0), 0);
  const total = p.price * qty + addonTotal;
  const ready = !box || (chutney && salad);

  function addAll(): boolean {
    setTried(true);
    if (!ready) return false;
    cart.add({ slug: p.slug, qty, chutney, salad }, { open: false });
    for (const a of ADDONS) if (addons[a.slug]) cart.add({ slug: a.slug, qty: addons[a.slug] }, { open: false });
    return true;
  }

  const waText = [
    `Assalam o Alaikum, I want to order:`,
    `${qty} × ${p.name}${box ? ` (${chutney ?? "chutney?"} chutney, ${salad ?? "salad?"} salad)` : ""}`,
    ...ADDONS.filter((a) => addons[a.slug]).map((a) => `${addons[a.slug]} × ${a.name}`),
    `Total ${formatPKR(total)} + delivery`,
  ].join("\n");

  return (
    <div>
      <h1 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">{p.name}</h1>
      {productMeta(p) && (
        <p className="mt-3 text-ash-300">
          {productMeta(p)}
          {box && " · grill & coal included"}
        </p>
      )}
      <p className="mt-4 max-w-[52ch] leading-relaxed text-ash-300">{p.description}</p>
      <div className="mt-6 flex items-baseline gap-3">
        <span className="font-mono text-3xl font-semibold text-ember-400">{formatPKR(p.price)}</span>
        {saving && (
          <span className="rounded-full bg-ember-500/15 px-3 py-1 text-sm font-medium text-ember-300">
            Save {formatPKR(saving)} vs separate boxes
          </span>
        )}
      </div>

      {p.soldOut ? (
        <p className="mt-8 rounded-[var(--radius-card)] bg-coal-900 p-5 text-ash-300">Sold out today. Check back tomorrow.</p>
      ) : (
        <>
          {box && (
            <div className="mt-8 space-y-6 rounded-[var(--radius-card)] bg-coal-900 p-6 ring-1 ring-white/[0.06]">
              <ChoicePills name="chutney" label="Choose your chutney" options={CHUTNEYS} value={chutney} onChange={setChutney} error={tried && !chutney} />
              <ChoicePills name="salad" label="Choose your salad" options={SALADS} value={salad} onChange={setSalad} error={tried && !salad} />
            </div>
          )}

          <div className="mt-6 rounded-[var(--radius-card)] bg-coal-900 p-6 ring-1 ring-white/[0.06]">
            <p className="text-sm font-medium text-ash-100">Add extras (optional)</p>
            <ul className="mt-4 space-y-3">
              {ADDONS.filter((a) => a.slug !== p.slug).map((a) => (
                <li key={a.slug} className="flex items-center justify-between gap-4">
                  <span className="text-sm text-ash-100">
                    {a.name} <span className="font-mono text-ash-400">{formatPKR(a.price)}</span>
                  </span>
                  <Stepper
                    label={a.name}
                    value={addons[a.slug] ?? 0}
                    onChange={(n) => setAddons((cur) => ({ ...cur, [a.slug]: n }))}
                  />
                </li>
              ))}
            </ul>
          </div>

          <div className="mt-6 flex items-center justify-between gap-4">
            <span className="text-sm font-medium text-ash-100">Quantity</span>
            <Stepper label="boxes" min={1} value={qty} onChange={setQty} />
          </div>

          <div className="mt-6 flex items-center justify-between border-t border-white/[0.06] pt-6">
            <span className="text-ash-300">Total</span>
            <span className="font-mono text-2xl font-semibold text-ash-50" aria-live="polite">
              {formatPKR(total)}
            </span>
          </div>

          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            <Button size="lg" onClick={() => addAll() && cart.setOpen(true)}>
              Add to cart
            </Button>
            <Button
              size="lg"
              variant="ghost"
              onClick={() => {
                if (addAll()) router.push("/checkout");
              }}
            >
              Order now
            </Button>
          </div>
          {tried && !ready && <p role="alert" className="mt-3 text-sm text-danger">Choose a chutney and a salad first.</p>}
          <a
            href={whatsappLink(waText)}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-4 inline-flex items-center gap-2 text-sm text-ash-300 underline-offset-4 hover:text-ash-50 hover:underline"
          >
            <WhatsappLogo size={18} weight="fill" className="text-[#25d366]" /> Order this on WhatsApp instead
          </a>
        </>
      )}

      {box && (
        <div className="mt-10">
          <h2 className="text-lg font-semibold text-ash-50">What&apos;s in the box</h2>
          <ul className="mt-4 grid gap-3 sm:grid-cols-2">
            {BOX_INCLUDES.map((i) => (
              <li key={i} className="flex items-center gap-2 text-ash-300">
                <CheckCircle size={20} weight="fill" className="shrink-0 text-ember-400" /> {i}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
