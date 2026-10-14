"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { ArrowsClockwise, Minus, Money, Plus, Truck } from "@phosphor-icons/react";
import { images, product, shipping, type ImageSlot as Slot } from "@/lib/catalog";
import { formatPKR } from "@/lib/pricing";
import { useCart } from "../cart/CartProvider";
import { ImageSlot } from "../ImageSlot";
import { MotionImage } from "../MotionImage";
import { Button, Container } from "../ui";

const gallery: Slot[] = [images.hero, images.build, images.portable, images.clean];

export function BuyBox() {
  const cart = useCart();
  const router = useRouter();
  const [bundleId, setBundleId] = useState<string>(
    product.bundles.find((b) => b.highlight)?.id ?? product.bundles[0].id,
  );
  const [qty, setQty] = useState(1);
  const [shot, setShot] = useState(0);
  const bundle = product.bundles.find((b) => b.id === bundleId)!;

  return (
    <section id="shop" className="scroll-mt-16 border-t border-white/[0.06] bg-coal-900 py-24 md:py-32">
      <Container className="grid gap-12 lg:grid-cols-[1.1fr_1fr] lg:gap-16">
        <div className="lg:sticky lg:top-24 lg:self-start">
          <div className="relative aspect-square overflow-hidden rounded-[var(--radius-card)] ring-1 ring-white/[0.06]">
            {shot === 0 ? (
              <MotionImage slot={gallery[0]} radius="20px" parallax={0} sizes="(min-width: 1024px) 50vw, 100vw" className="h-full w-full" />
            ) : (
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.div
                  key={shot}
                  className="absolute inset-0"
                  initial={{ opacity: 0, scale: 1.08 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
                >
                  <ImageSlot slot={gallery[shot]} sizes="(min-width: 1024px) 50vw, 100vw" className="h-full w-full" />
                </motion.div>
              </AnimatePresence>
            )}
          </div>
          <div className="mt-3 grid grid-cols-4 gap-3">
            {gallery.map((g, i) => (
              <button
                key={g.alt}
                type="button"
                onClick={() => setShot(i)}
                aria-label={`Show photo: ${g.alt}`}
                aria-pressed={shot === i}
                className={`overflow-hidden rounded-[var(--radius-field)] ring-2 transition ${
                  shot === i ? "ring-ember-500" : "ring-transparent opacity-60 hover:opacity-100"
                }`}
              >
                <ImageSlot
                  slot={{ ...g, brief: "" }}
                  sizes="120px"
                  className="aspect-square transition-transform duration-500 hover:scale-110"
                />
              </button>
            ))}
          </div>
        </div>

        <div>
          <h2 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">{product.name}</h2>
          <p className="mt-4 max-w-[48ch] text-lg leading-relaxed text-ash-300">{product.summary}</p>

          <div className="mt-8 flex items-baseline gap-3">
            <span className="font-mono text-3xl font-semibold text-ember-400">{formatPKR(bundle.price)}</span>
            {bundle.compareAt && (
              <>
                <span className="font-mono text-lg text-ash-400 line-through">{formatPKR(bundle.compareAt)}</span>
                <span className="rounded-full bg-ember-500/15 px-3 py-1 text-sm font-medium text-ember-300">
                  Save {formatPKR(bundle.compareAt - bundle.price)}
                </span>
              </>
            )}
          </div>

          <fieldset className="mt-8">
            <legend className="text-sm font-medium text-ash-50">Choose your kit</legend>
            <div className="mt-3 space-y-3">
              {product.bundles.map((b) => (
                <label
                  key={b.id}
                  className={`flex cursor-pointer items-center gap-4 rounded-[var(--radius-card)] p-5 ring-1 transition ${
                    bundleId === b.id
                      ? "bg-coal-800 ring-ember-500"
                      : "bg-coal-850 ring-white/[0.06] hover:ring-coal-600"
                  }`}
                >
                  <input
                    type="radio"
                    name="bundle"
                    value={b.id}
                    checked={bundleId === b.id}
                    onChange={() => setBundleId(b.id)}
                    className="size-5 accent-[var(--color-ember-500)]"
                  />
                  <span className="flex-1">
                    <span className="flex items-center gap-2 font-medium text-ash-50">
                      {b.name}
                      {b.highlight && (
                        <span className="rounded-full bg-ember-500 px-2.5 py-0.5 text-xs font-semibold text-coal-950">
                          Most popular
                        </span>
                      )}
                    </span>
                    <span className="mt-1 block text-sm text-ash-400">{b.contents}</span>
                  </span>
                  <span className="font-mono text-ash-100">{formatPKR(b.price)}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <div className="flex h-13 items-center rounded-full border border-coal-600">
              <button
                type="button"
                className="grid size-13 place-items-center text-ash-300 hover:text-ash-50"
                onClick={() => setQty((q) => Math.max(1, q - 1))}
                aria-label="Decrease quantity"
              >
                <Minus size={16} />
              </button>
              <span className="w-8 text-center font-mono" aria-live="polite" aria-label={`Quantity ${qty}`}>
                {qty}
              </span>
              <button
                type="button"
                className="grid size-13 place-items-center text-ash-300 hover:text-ash-50"
                onClick={() => setQty((q) => Math.min(product.maxQtyPerLine, q + 1))}
                aria-label="Increase quantity"
              >
                <Plus size={16} />
              </button>
            </div>
            <Button size="lg" variant="ghost" className="flex-1" onClick={() => cart.add(bundleId, qty)}>
              Add to cart
            </Button>
          </div>
          <Button
            size="lg"
            className="mt-3 w-full"
            onClick={() => {
              cart.add(bundleId, qty);
              cart.setOpen(false);
              router.push("/checkout");
            }}
          >
            Order now, pay on delivery
          </Button>

          <ul className="mt-8 space-y-3 text-sm text-ash-300">
            <li className="flex items-center gap-3">
              <Truck size={20} className="text-ember-400" />
              {shipping.eta}. Free over {formatPKR(shipping.freeAbove)}.
            </li>
            <li className="flex items-center gap-3">
              <Money size={20} className="text-ember-400" />
              Cash on delivery. We confirm on WhatsApp before dispatch.
            </li>
            <li className="flex items-center gap-3">
              <ArrowsClockwise size={20} className="text-ember-400" />
              {shipping.replacementDays}-day replacement if it arrives damaged.
            </li>
          </ul>
        </div>
      </Container>
    </section>
  );
}
