"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { API_BASE } from "@/lib/api";
import { SIGNUP_URL } from "@/lib/links";
import { PACKAGES, ROWS, USD_TO_PKR, type Currency } from "./packages";

type LivePlan = { key: string; price_pkr: number | null; price_usd: number | null };

const n = (v: number) => Math.round(v).toLocaleString("en-US");

// The visitor's currency: their own choice if they made one, otherwise rupees
// in Pakistan and dollars everywhere else, as the market prices it. The server
// (and search engines) render rupees.
const CUR_EVENT = "td-currency";
function readCurrency(): Currency {
  try {
    const saved = localStorage.getItem("td-currency");
    if (saved === "PKR" || saved === "USD") return saved;
  } catch {
    /* private mode */
  }
  return Intl.DateTimeFormat().resolvedOptions().timeZone === "Asia/Karachi" ? "PKR" : "USD";
}
function subscribe(cb: () => void) {
  window.addEventListener(CUR_EVENT, cb);
  window.addEventListener("storage", cb);
  return () => {
    window.removeEventListener(CUR_EVENT, cb);
    window.removeEventListener("storage", cb);
  };
}

export function PricingCards() {
  const cur = useSyncExternalStore<Currency>(subscribe, readCurrency, () => "PKR");
  const [live, setLive] = useState<Record<string, LivePlan>>({});

  useEffect(() => {
    // Prices come from the founder console, so a change there shows here.
    fetch(`${API_BASE}/plans`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && Array.isArray(d.plans)) setLive(Object.fromEntries(d.plans.map((p: LivePlan) => [p.key, p])));
      })
      .catch(() => {});
  }, []);

  function pick(c: Currency) {
    try {
      localStorage.setItem("td-currency", c);
    } catch {
      /* private mode: the choice lasts until the page closes */
    }
    window.dispatchEvent(new Event(CUR_EVENT));
  }

  // one conversion for every figure, so the rows add up to the total shown
  const conv = (usd: number) => (cur === "USD" ? usd : Math.round((usd * USD_TO_PKR) / 100) * 100);
  const money = (usd: number) => `${cur} ${n(conv(usd))}`;

  return (
    <div className="mt-8">
      <div className="mb-6 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-[var(--muted)]">Show prices in</span>
        {(["PKR", "USD"] as Currency[]).map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => pick(c)}
            aria-pressed={cur === c}
            className={`rounded-full border px-3 py-1 font-mono text-xs ${
              cur === c ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]" : "border-white/10 text-white"
            }`}
          >
            {c}
          </button>
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        {PACKAGES.map((pkg, i) => {
          const lp = live[pkg.key];
          const price =
            cur === "USD" ? (lp?.price_usd ?? pkg.usd) : (lp?.price_pkr ?? pkg.pkr);
          let totalShown = 0;
          ROWS.forEach((r) => {
            const v = r.tiers[i];
            if (v) totalShown += conv(v[1]);
          });
          const save = totalShown - price;
          return (
            <section
              key={pkg.key}
              aria-label={`${pkg.name} package`}
              className={`td-card flex min-w-0 flex-col p-6 ${pkg.popular ? "ring-1 ring-[var(--accent)]" : ""}`}
            >
              <div className="flex items-start justify-between gap-2">
                <h2 className="text-xl font-semibold text-white">{pkg.name}</h2>
                {pkg.popular ? (
                  <span className="rounded-full bg-[var(--accent)] px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-white">
                    Most popular
                  </span>
                ) : null}
              </div>
              <p className="mt-1 text-sm text-[var(--muted)]">{pkg.tagline}</p>
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <span className="rounded-md border border-white/10 px-2 py-1">
                  <b className="font-mono text-[var(--accent)]">{pkg.numbers}</b> WhatsApp number{pkg.numbers === 1 ? "" : "s"}
                </span>
                <span className="rounded-md border border-white/10 px-2 py-1">
                  <b className="font-mono text-[var(--accent)]">{n(pkg.chats)}</b> AI chats / month
                </span>
              </div>

              <ul className="mt-3">
                {ROWS.map((r) => {
                  const v = r.tiers[i];
                  return (
                    <li key={r.name} className="flex items-center justify-between gap-3 border-b border-white/[0.08] py-2.5">
                      <div className="min-w-0">
                        <div className={`text-sm font-medium ${v ? "text-white" : "text-white/30"}`}>{r.name}</div>
                        <div className={`text-xs ${v ? "text-[var(--muted)]" : "text-white/25"}`}>
                          {v ? `Replaces ${v[0]}` : "In a bigger package"}
                        </div>
                      </div>
                      <div className={`whitespace-nowrap text-right font-mono text-sm tabular-nums ${v ? "text-white" : "text-xs text-white/30"}`}>
                        {v ? money(v[1]) : "Not included"}
                      </div>
                    </li>
                  );
                })}
              </ul>

              <div className="flex flex-wrap items-baseline justify-between gap-2 py-3 text-sm text-[var(--muted)]">
                <span>What you&apos;d spend otherwise</span>
                <span className="font-mono text-[var(--danger)] line-through decoration-2">
                  {cur} {n(totalShown)} / mo
                </span>
              </div>

              <div className="mt-auto rounded-xl border border-[var(--accent)] bg-[var(--accent-soft)] p-4">
                <div className="font-mono text-2xl font-semibold text-white tabular-nums">
                  {cur} {n(price)}
                  <span className="font-sans text-sm font-normal text-[var(--muted)]"> / month</span>
                </div>
                {save > 0 ? (
                  <div className="mt-1 text-xs font-semibold text-[var(--success)]">
                    You save {cur} {n(save)} a month ({Math.round((save * 100) / totalShown)}% less)
                  </div>
                ) : null}
                <a href={SIGNUP_URL} className="td-btn-primary mt-3 inline-block px-4 py-2 text-sm">
                  Start 7-day free trial
                </a>
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
