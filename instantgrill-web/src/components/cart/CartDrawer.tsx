"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Handbag, Minus, Plus, Trash, X } from "@phosphor-icons/react";
import { findProduct, products } from "@/lib/catalog";
import { formatPKR, lineKey } from "@/lib/pricing";
import { useCart } from "./CartProvider";
import { buttonClass } from "../ui";

const UPSELL = ["drink-500", "drink-1-5l", "extra-chicken-skewer", "extra-beef-skewer"];

export function CartDrawer() {
  const cart = useCart();
  const reduce = useReducedMotion();
  const panel = useRef<HTMLDivElement>(null);
  const { open, setOpen } = cart;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    panel.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, setOpen]);

  const upsell = products.filter((p) => UPSELL.includes(p.slug));

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-coal-950/70 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setOpen(false)}
          />
          <motion.div
            ref={panel}
            role="dialog"
            aria-modal="true"
            aria-label="Your cart"
            tabIndex={-1}
            className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-white/[0.06] bg-coal-900 outline-none"
            initial={reduce ? { opacity: 0 } : { x: "100%" }}
            animate={reduce ? { opacity: 1 } : { x: 0 }}
            exit={reduce ? { opacity: 0 } : { x: "100%" }}
            transition={{ type: "spring", stiffness: 260, damping: 32 }}
          >
            <div className="flex items-center justify-between border-b border-white/[0.06] px-6 py-5">
              <h2 className="text-lg font-semibold text-ash-50">Your cart</h2>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="grid size-10 place-items-center rounded-full text-ash-300 hover:bg-coal-800 hover:text-ash-50"
                aria-label="Close cart"
              >
                <X size={20} />
              </button>
            </div>

            {cart.lines.length === 0 ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
                <span className="grid size-16 place-items-center rounded-full bg-coal-800 text-ash-400">
                  <Handbag size={28} />
                </span>
                <p className="text-ash-300">Your cart is empty.</p>
                <Link href="/menu" onClick={() => setOpen(false)} className={buttonClass("ghost")}>
                  Browse the menu
                </Link>
              </div>
            ) : (
              <>
                <div className="flex-1 overflow-y-auto px-6 py-6">
                  <ul className="space-y-4">
                    {cart.lines.map((line) => {
                      const p = findProduct(line.slug);
                      if (!p) return null;
                      const key = lineKey(line);
                      return (
                        <li key={key} className="rounded-[var(--radius-card)] bg-coal-850 p-4">
                          <div className="flex items-start justify-between gap-4">
                            <div>
                              <p className="font-medium text-ash-50">{p.name}</p>
                              {line.chutney && (
                                <p className="mt-1 text-sm text-ash-400">
                                  {line.chutney} chutney, {line.salad} salad
                                </p>
                              )}
                            </div>
                            <button
                              type="button"
                              onClick={() => cart.remove(key)}
                              className="grid size-9 shrink-0 place-items-center rounded-full text-ash-400 hover:bg-coal-700 hover:text-ash-50"
                              aria-label={`Remove ${p.name}`}
                            >
                              <Trash size={18} />
                            </button>
                          </div>
                          <div className="mt-4 flex items-center justify-between">
                            <div className="flex items-center rounded-full border border-coal-600">
                              <button
                                type="button"
                                className="grid size-9 place-items-center text-ash-300 hover:text-ash-50"
                                onClick={() => cart.setQty(key, line.qty - 1)}
                                aria-label={`Decrease ${p.name} quantity`}
                              >
                                <Minus size={14} />
                              </button>
                              <span className="w-8 text-center font-mono text-sm" aria-live="polite">
                                {line.qty}
                              </span>
                              <button
                                type="button"
                                className="grid size-9 place-items-center text-ash-300 hover:text-ash-50"
                                onClick={() => cart.setQty(key, line.qty + 1)}
                                aria-label={`Increase ${p.name} quantity`}
                              >
                                <Plus size={14} />
                              </button>
                            </div>
                            <span className="font-mono text-ash-50">{formatPKR(p.price * line.qty)}</span>
                          </div>
                        </li>
                      );
                    })}
                  </ul>

                  <p className="mt-8 text-sm font-medium text-ash-100">Add to your order</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {upsell.map((p) => (
                      <button
                        key={p.slug}
                        type="button"
                        onClick={() => cart.add({ slug: p.slug, qty: 1 }, { open: true })}
                        className="inline-flex items-center gap-1.5 rounded-full border border-coal-600 px-3 py-2 text-sm text-ash-100 transition hover:border-ember-500 active:scale-[0.97]"
                      >
                        <Plus size={14} className="text-ember-400" /> {p.name}
                        <span className="font-mono text-ash-400">{formatPKR(p.price)}</span>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="space-y-4 border-t border-white/[0.06] px-6 py-6">
                  <dl className="space-y-2 text-sm">
                    <div className="flex justify-between text-ash-300">
                      <dt>Subtotal</dt>
                      <dd className="font-mono">{formatPKR(cart.subtotal)}</dd>
                    </div>
                    <div className="flex justify-between text-ash-300">
                      <dt>Delivery</dt>
                      <dd>{cart.deliveryFee === null ? "Calculated at checkout" : formatPKR(cart.deliveryFee)}</dd>
                    </div>
                  </dl>
                  <Link href="/checkout" onClick={() => setOpen(false)} className={buttonClass("primary", "lg", "w-full")}>
                    Checkout, pay on delivery
                  </Link>
                </div>
              </>
            )}
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
