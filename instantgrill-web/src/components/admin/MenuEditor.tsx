"use client";

import { useState } from "react";
import { Check, Plus, Trash, WarningCircle } from "@phosphor-icons/react";
import { CATEGORIES, type Product } from "@/lib/catalog";
import type { Area } from "@/lib/menu-types";
import { formatPKR } from "@/lib/pricing";
import { Button } from "../ui";

type Row = Product & { active: boolean; basePrice: number };

const field =
  "h-10 rounded-[var(--radius-field)] border border-coal-600 bg-coal-850 px-3 text-sm text-ash-50 focus:border-ember-500 focus:outline-none";

function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition ${on ? "bg-ember-500" : "bg-coal-700"}`}
    >
      <span className={`absolute left-0.5 top-0.5 size-5 rounded-full bg-ash-50 transition-transform ${on ? "translate-x-5" : "translate-x-0"}`} />
    </button>
  );
}

function ProductRow({ row }: { row: Row }) {
  const [p, setP] = useState(row);
  const [price, setPrice] = useState(String(row.price));
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [msg, setMsg] = useState("");

  async function save(patch: { price?: number; soldOut?: boolean; active?: boolean }) {
    setState("saving");
    const res = await fetch(`/api/admin/menu/${p.slug}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(patch),
    });
    const data = await res.json();
    if (!res.ok) {
      setState("error");
      setMsg(data.error);
      return;
    }
    setP((cur) => ({ ...cur, ...patch }));
    setState("saved");
    setTimeout(() => setState("idle"), 1500);
  }

  const priceChanged = Number(price) !== p.price;
  return (
    <li className={`flex flex-wrap items-center gap-x-6 gap-y-3 py-4 ${p.active ? "" : "opacity-50"}`}>
      <div className="min-w-48 flex-1">
        <p className="font-medium text-ash-50">{p.name}</p>
        {p.price !== p.basePrice && <p className="text-xs text-ash-400">List price {formatPKR(p.basePrice)}</p>}
      </div>
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          save({ price: Number(price) });
        }}
      >
        <label className="sr-only" htmlFor={`price-${p.slug}`}>
          Price for {p.name}
        </label>
        <span className="text-sm text-ash-400">Rs</span>
        <input id={`price-${p.slug}`} inputMode="numeric" value={price} onChange={(e) => setPrice(e.target.value.replace(/\D/g, ""))} className={`${field} w-24 font-mono`} />
        {priceChanged && (
          <Button type="submit" className="h-10 px-4" disabled={state === "saving"}>
            Save
          </Button>
        )}
      </form>
      <label className="flex items-center gap-2 text-sm text-ash-300">
        <Toggle on={!!p.soldOut} onChange={(v) => save({ soldOut: v })} label={`${p.name} sold out`} /> Sold out
      </label>
      <label className="flex items-center gap-2 text-sm text-ash-300">
        <Toggle on={p.active} onChange={(v) => save({ active: v })} label={`${p.name} shown on menu`} /> On menu
      </label>
      <span className="w-16 text-xs" aria-live="polite">
        {state === "saved" && (
          <span className="inline-flex items-center gap-1 text-ok">
            <Check size={14} /> Saved
          </span>
        )}
        {state === "error" && (
          <span className="inline-flex items-center gap-1 text-danger" title={msg}>
            <WarningCircle size={14} /> {msg || "Error"}
          </span>
        )}
      </span>
    </li>
  );
}

function AreasEditor({ initial }: { initial: Area[] }) {
  const [areas, setAreas] = useState(initial.map((a) => ({ ...a, feeText: a.fee === null ? "" : String(a.fee) })));
  const [state, setState] = useState<{ kind: "idle" | "saving" | "saved" | "error"; msg?: string }>({ kind: "idle" });
  const update = (i: number, patch: Partial<(typeof areas)[number]>) => setAreas((cur) => cur.map((a, j) => (j === i ? { ...a, ...patch } : a)));

  async function save() {
    setState({ kind: "saving" });
    const res = await fetch("/api/admin/areas", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ areas: areas.map((a) => ({ name: a.name, fee: a.feeText === "" ? null : Number(a.feeText), active: a.active })) }),
    });
    const data = await res.json();
    setState(res.ok ? { kind: "saved" } : { kind: "error", msg: data.error });
  }

  return (
    <div className="rounded-[var(--radius-card)] bg-coal-900 p-6 ring-1 ring-white/[0.06]">
      <p className="text-sm text-ash-400">Leave the fee empty to confirm it with the customer on WhatsApp.</p>
      <ul className="mt-4 space-y-2">
        {areas.map((a, i) => (
          <li key={i} className="flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor={`area-${i}`}>
              Area name
            </label>
            <input id={`area-${i}`} value={a.name} onChange={(e) => update(i, { name: e.target.value })} className={`${field} min-w-0 flex-1`} />
            <label className="sr-only" htmlFor={`fee-${i}`}>
              Fee for {a.name}
            </label>
            <span className="text-sm text-ash-400">Rs</span>
            <input id={`fee-${i}`} inputMode="numeric" placeholder="TBC" value={a.feeText} onChange={(e) => update(i, { feeText: e.target.value.replace(/\D/g, "") })} className={`${field} w-24 font-mono`} />
            <Toggle on={a.active} onChange={(v) => update(i, { active: v })} label={`${a.name || "Area"} active`} />
            <button type="button" aria-label={`Remove ${a.name}`} onClick={() => setAreas((cur) => cur.filter((_, j) => j !== i))} className="grid size-10 place-items-center rounded-full text-ash-400 hover:bg-coal-800 hover:text-danger">
              <Trash size={16} />
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button variant="ghost" onClick={() => setAreas((cur) => [...cur, { name: "", fee: null, active: true, feeText: "" }])}>
          <Plus size={16} /> Add area
        </Button>
        <Button onClick={save} disabled={state.kind === "saving"}>
          {state.kind === "saving" ? "Saving..." : "Save areas"}
        </Button>
        {state.kind === "saved" && <span className="text-sm text-ok">Saved. Checkout uses these now.</span>}
        {state.kind === "error" && <span className="text-sm text-danger">{state.msg}</span>}
      </div>
    </div>
  );
}

export function MenuEditor({ products, areas }: { products: Row[]; areas: Area[] }) {
  return (
    <div className="space-y-12">
      {CATEGORIES.map((c) => {
        const rows = products.filter((p) => p.category === c.id);
        return (
          <section key={c.id}>
            <h2 className="text-lg font-semibold text-ash-50">{c.label}</h2>
            <ul className="mt-2 divide-y divide-white/[0.06] rounded-[var(--radius-card)] bg-coal-900 px-6 ring-1 ring-white/[0.06]">
              {rows.map((r) => (
                <ProductRow key={r.slug} row={r} />
              ))}
            </ul>
          </section>
        );
      })}
      <section>
        <h2 className="mb-2 text-lg font-semibold text-ash-50">Delivery areas and fees</h2>
        <AreasEditor initial={areas} />
      </section>
    </div>
  );
}
