"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useReducer } from "react";
import { delivery, findProduct } from "@/lib/catalog";
import { MAX_QTY_PER_LINE, lineKey, type CartLine } from "@/lib/pricing";

type State = { lines: CartLine[]; open: boolean; hydrated: boolean };
type Action =
  | { type: "hydrate"; lines: CartLine[] }
  | { type: "add"; line: CartLine; open: boolean }
  | { type: "set"; key: string; qty: number }
  | { type: "remove"; key: string }
  | { type: "clear" }
  | { type: "open"; open: boolean };

const STORAGE_KEY = "ig.cart.v2";

const clampQty = (n: number) => Math.max(1, Math.min(MAX_QTY_PER_LINE, Math.floor(n) || 1));

function reducer(state: State, a: Action): State {
  switch (a.type) {
    case "hydrate":
      return { ...state, lines: a.lines, hydrated: true };
    case "add": {
      const key = lineKey(a.line);
      const existing = state.lines.find((l) => lineKey(l) === key);
      const lines = existing
        ? state.lines.map((l) => (lineKey(l) === key ? { ...l, qty: clampQty(l.qty + a.line.qty) } : l))
        : [...state.lines, { ...a.line, qty: clampQty(a.line.qty) }];
      return { ...state, lines, open: a.open || state.open };
    }
    case "set":
      return { ...state, lines: state.lines.map((l) => (lineKey(l) === a.key ? { ...l, qty: clampQty(a.qty) } : l)) };
    case "remove":
      return { ...state, lines: state.lines.filter((l) => lineKey(l) !== a.key) };
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
  deliveryFee: number | null;
  total: number;
  add: (line: CartLine, opts?: { open?: boolean }) => void;
  setQty: (key: string, qty: number) => void;
  remove: (key: string) => void;
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
      if (Array.isArray(parsed)) saved = parsed.filter((l) => findProduct(l?.slug) && l.qty > 0);
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

  const add = useCallback(
    (line: CartLine, opts?: { open?: boolean }) => dispatch({ type: "add", line, open: opts?.open ?? true }),
    [],
  );
  const setQty = useCallback((key: string, qty: number) => dispatch({ type: "set", key, qty }), []);
  const remove = useCallback((key: string) => dispatch({ type: "remove", key }), []);
  const clear = useCallback(() => dispatch({ type: "clear" }), []);
  const setOpen = useCallback((open: boolean) => dispatch({ type: "open", open }), []);

  const value = useMemo<CartContext>(() => {
    const subtotal = state.lines.reduce((s, l) => s + (findProduct(l.slug)?.price ?? 0) * l.qty, 0);
    return {
      lines: state.lines,
      open: state.open,
      hydrated,
      count: state.lines.reduce((s, l) => s + l.qty, 0),
      subtotal,
      deliveryFee: delivery.fee,
      total: subtotal + (delivery.fee ?? 0),
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
