"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useReducer } from "react";
import { findBundle, product, shipping } from "@/lib/catalog";
import type { CartLine } from "@/lib/pricing";

type State = { lines: CartLine[]; open: boolean; hydrated: boolean };
type Action =
  | { type: "hydrate"; lines: CartLine[] }
  | { type: "add"; bundleId: string; qty: number }
  | { type: "set"; bundleId: string; qty: number }
  | { type: "remove"; bundleId: string }
  | { type: "clear" }
  | { type: "open"; open: boolean };

const STORAGE_KEY = "ig.cart.v1";

function clampQty(n: number) {
  return Math.max(1, Math.min(product.maxQtyPerLine, Math.floor(n) || 1));
}

function reducer(state: State, a: Action): State {
  switch (a.type) {
    case "hydrate":
      return { ...state, lines: a.lines, hydrated: true };
    case "add": {
      const existing = state.lines.find((l) => l.bundleId === a.bundleId);
      const lines = existing
        ? state.lines.map((l) => (l.bundleId === a.bundleId ? { ...l, qty: clampQty(l.qty + a.qty) } : l))
        : [...state.lines, { bundleId: a.bundleId, qty: clampQty(a.qty) }];
      return { ...state, lines, open: true };
    }
    case "set":
      return { ...state, lines: state.lines.map((l) => (l.bundleId === a.bundleId ? { ...l, qty: clampQty(a.qty) } : l)) };
    case "remove":
      return { ...state, lines: state.lines.filter((l) => l.bundleId !== a.bundleId) };
    case "clear":
      return { ...state, lines: [] };
    case "open":
      return { ...state, open: a.open };
  }
}

type CartContext = {
  lines: CartLine[];
  open: boolean;
  hydrated: boolean;
  count: number;
  /** Display estimate only. The server re-prices every order from the catalog. */
  subtotal: number;
  shippingFee: number;
  total: number;
  add: (bundleId: string, qty?: number) => void;
  setQty: (bundleId: string, qty: number) => void;
  remove: (bundleId: string) => void;
  clear: () => void;
  setOpen: (open: boolean) => void;
};

const Ctx = createContext<CartContext | null>(null);

export function CartProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, { lines: [], open: false, hydrated: false });
  const { hydrated } = state;

  // The cart is a per-browser convenience; storage can be missing or blocked.
  useEffect(() => {
    let saved: CartLine[] = [];
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      const parsed = raw ? (JSON.parse(raw) as CartLine[]) : [];
      if (Array.isArray(parsed)) saved = parsed.filter((l) => findBundle(l.bundleId) && l.qty > 0);
    } catch {
      /* start with an empty cart */
    }
    // First-touch attribution for this visit, sent with the order (which ad sold it).
    try {
      if (!sessionStorage.getItem("ig.source")) {
        const p = new URLSearchParams(location.search);
        sessionStorage.setItem(
          "ig.source",
          JSON.stringify({
            utmSource: p.get("utm_source") ?? undefined,
            utmCampaign: p.get("utm_campaign") ?? undefined,
            referrer: document.referrer || undefined,
          }),
        );
      }
    } catch {
      /* ignore */
    }
    dispatch({ type: "hydrate", lines: saved });
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state.lines));
    } catch {
      /* ignore */
    }
  }, [state.lines, hydrated]);

  const add = useCallback((bundleId: string, qty = 1) => dispatch({ type: "add", bundleId, qty }), []);
  const setQty = useCallback((bundleId: string, qty: number) => dispatch({ type: "set", bundleId, qty }), []);
  const remove = useCallback((bundleId: string) => dispatch({ type: "remove", bundleId }), []);
  const clear = useCallback(() => dispatch({ type: "clear" }), []);
  const setOpen = useCallback((open: boolean) => dispatch({ type: "open", open }), []);

  const value = useMemo<CartContext>(() => {
    const subtotal = state.lines.reduce((s, l) => s + (findBundle(l.bundleId)?.price ?? 0) * l.qty, 0);
    const shippingFee = subtotal === 0 || subtotal >= shipping.freeAbove ? 0 : shipping.fee;
    return {
      lines: state.lines,
      open: state.open,
      hydrated,
      count: state.lines.reduce((s, l) => s + l.qty, 0),
      subtotal,
      shippingFee,
      total: subtotal + shippingFee,
      add,
      setQty,
      remove,
      clear,
      setOpen,
    };
  }, [state, hydrated, add, setQty, remove, clear, setOpen]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCart() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useCart must be used inside CartProvider");
  return ctx;
}
