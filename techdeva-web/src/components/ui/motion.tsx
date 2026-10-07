"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { cn } from "@/lib/cn";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(cb: () => void) {
  const mq = window.matchMedia(QUERY);
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
}

/** True when the user asked the OS for less motion. Server render assumes reduced. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(QUERY).matches,
    () => true,
  );
}

const easeOutExpo = (t: number) => (t === 1 ? 1 : 1 - Math.pow(2, -10 * t));

/**
 * Counts up to `value` once it scrolls into view.
 *
 * Ported from 21st.dev "Number Ticker" (danielpetho) without the motion
 * dependency: one requestAnimationFrame loop, no re-render per frame (the text
 * node is written directly). Under reduced motion it renders the final value.
 * The final value is always what assistive tech reads.
 */
export function CountUp({
  value,
  format = (n) => Math.round(n).toLocaleString(),
  duration = 1100,
  delay = 0,
  className,
}: {
  value: number;
  format?: (n: number) => string;
  duration?: number;
  delay?: number;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const reduced = useReducedMotion();
  const finalText = format(value);

  useEffect(() => {
    const el = ref.current;
    if (!el || reduced) return;
    let raf = 0;
    let timer: ReturnType<typeof setTimeout>;
    const run = () => {
      const start = performance.now();
      const tick = (now: number) => {
        const t = Math.min(1, (now - start) / duration);
        el.textContent = format(value * easeOutExpo(t));
        if (t < 1) raf = requestAnimationFrame(tick);
      };
      el.textContent = format(0);
      raf = requestAnimationFrame(tick);
    };
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          io.disconnect();
          timer = setTimeout(run, delay);
        }
      },
      { threshold: 0.4 },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      clearTimeout(timer);
      cancelAnimationFrame(raf);
      el.textContent = finalText;
    };
    // format is expected to be stable per value; finalText captures it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, duration, delay, reduced, finalText]);

  return (
    <span className={cn("tabular-nums", className)}>
      <span aria-hidden="true" ref={ref}>
        {finalText}
      </span>
      <span className="sr-only">{finalText}</span>
    </span>
  );
}

/**
 * A card wrapper whose border lights up toward the pointer.
 *
 * Ported from 21st.dev "Glowing Effect" (manuarora700, as on Cursor's site),
 * rebuilt on CSS custom properties with a rAF-eased angle. Only active for a
 * fine pointer with hover and without reduced motion; elsewhere it renders a
 * plain card, so phones pay nothing.
 */
export function GlowCard({
  className,
  children,
  as: Tag = "div",
}: {
  className?: string;
  children: React.ReactNode;
  as?: "div" | "li" | "article";
}) {
  const ref = useRef<HTMLElement>(null);
  const reduced = useReducedMotion();
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    // Read once after mount; a pointer-type change mid-session is not worth a listener.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEnabled(!reduced && window.matchMedia("(hover: hover) and (pointer: fine)").matches);
  }, [reduced]);

  useEffect(() => {
    const el = ref.current;
    if (!el || !enabled) return;
    let current = 0;
    let target = 0;
    let raf = 0;
    const step = () => {
      const diff = ((target - current + 540) % 360) - 180;
      current += diff * 0.12;
      el.style.setProperty("--glow-angle", current.toFixed(1));
      if (Math.abs(diff) > 0.5) raf = requestAnimationFrame(step);
      else raf = 0;
    };
    const onMove = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      const pad = 80;
      const inside =
        e.clientX > r.left - pad && e.clientX < r.right + pad && e.clientY > r.top - pad && e.clientY < r.bottom + pad;
      el.style.setProperty("--glow-on", inside ? "1" : "0");
      if (!inside) return;
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      target = (Math.atan2(e.clientY - cy, e.clientX - cx) * 180) / Math.PI + 90;
      if (!raf) raf = requestAnimationFrame(step);
    };
    document.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      document.removeEventListener("pointermove", onMove);
      cancelAnimationFrame(raf);
    };
  }, [enabled]);

  return (
    <Tag ref={ref as React.Ref<never>} className={cn("relative", className)}>
      {enabled ? <span aria-hidden="true" className="td-glow-edge" /> : null}
      {children}
    </Tag>
  );
}
