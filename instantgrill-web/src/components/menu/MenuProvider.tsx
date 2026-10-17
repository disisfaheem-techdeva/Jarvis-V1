"use client";

import { createContext, useContext } from "react";
import type { MenuData } from "@/lib/menu-types";

const Ctx = createContext<MenuData | null>(null);

/** The live menu (owner's prices, sold-out flags, delivery areas), loaded once on the server. */
export function MenuProvider({ menu, children }: { menu: MenuData; children: React.ReactNode }) {
  return <Ctx.Provider value={menu}>{children}</Ctx.Provider>;
}

export function useMenu(): MenuData {
  const m = useContext(Ctx);
  if (!m) throw new Error("useMenu must be used inside MenuProvider");
  return m;
}
