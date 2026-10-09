"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { buttonClass } from "@/components/ui/Button";
import { API_BASE } from "@/lib/api";
import { cn } from "@/lib/cn";
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
      <div className="mb-6 flex flex-wrap items-center gap-2 text-sm" role="group" aria-label="Currency">
        <span className="text-fg-muted">Show prices in</span>
        {(["PKR", "USD"] as Currency[]).map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => pick(c)}
            aria-pressed={cur === c}
            className={cn(
              "inline-flex h-9 items-center rounded-full border px-3.5 font-mono text-xs transition-colors",
              cur === c ? "border-accent-line bg-accent-tint text-accent-ink" : "border-line-strong text-fg-muted hover:text-fg",
            )}
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
              className={cn(
                "relative flex min-w-0 flex-col rounded-[var(--p-radius-xl)] border p-6",
                pkg.popular ? "td-glass td-rim border-accent-line" : "border-line bg-surface",
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <h2 className="font-display text-2xl font-semibold tracking-tight text-fg">{pkg.name}</h2>
                {pkg.popular ? (
                  <span className="inline-flex h-6 items-center rounded-full bg-accent px-2.5 text-[11px] font-semibold uppercase tracking-wider text-on-accent">
                    Most popular
                  </span>
                ) : null}
              </div>
              <p className="mt-1 text-sm text-fg-muted">{pkg.tagline}</p>
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <span className="rounded-[var(--p-radius-sm)] border border-line-strong px-2 py-1 text-fg-muted">
                  <b className="font-mono text-accent-ink">{pkg.numbers}</b> WhatsApp number{pkg.numbers === 1 ? "" : "s"}
                </span>
                <span className="rounded-[var(--p-radius-sm)] border border-line-strong px-2 py-1 text-fg-muted">
                  <b className="font-mono text-accent-ink">{n(pkg.chats)}</b> AI chats / month
                </span>
              </div>

              <ul className="mt-3">
                {ROWS.map((r) => {
                  const v = r.tiers[i];
                  return (
                    <li key={r.name} className="flex items-center justify-between gap-3 border-b border-line py-2.5">
                      <div className="min-w-0">
                        <div className={cn("text-sm font-medium", v ? "text-fg" : "text-fg-subtle line-through decoration-line-strong")}>{r.name}</div>
                        <div className="text-xs text-fg-subtle">
                          {v ? `Replaces ${v[0]}` : "In a bigger package"}
                        </div>
                      </div>
                      <div className={cn("whitespace-nowrap text-right font-mono tabular-nums", v ? "text-sm text-fg" : "text-xs text-fg-subtle")}>
                        {v ? money(v[1]) : "Not included"}
                      </div>
                    </li>
                  );
                })}
              </ul>

              <div className="flex flex-wrap items-baseline justify-between gap-2 py-3 text-sm text-fg-muted">
                <span>What you&apos;d spend otherwise</span>
                <span className="font-mono text-danger line-through decoration-2">
                  {cur} {n(totalShown)} / mo
                </span>
              </div>

              <div className="mt-auto rounded-[var(--p-radius-lg)] border border-accent-line bg-accent-tint p-4">
                <div className="font-display text-3xl font-semibold tracking-tight text-fg tabular-nums">
                  {cur} {n(price)}
                  <span className="font-sans text-sm font-normal tracking-normal text-fg-muted"> / month</span>
                </div>
                {save > 0 ? (
                  <div className="mt-1 text-xs font-semibold text-success">
                    You save {cur} {n(save)} a month ({Math.round((save * 100) / totalShown)}% less)
                  </div>
                ) : null}
                <a href={SIGNUP_URL} className={buttonClass("primary", "md", "mt-3 w-full")}>
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
