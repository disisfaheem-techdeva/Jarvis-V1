"use client";

import { useRef } from "react";
import { motion, useReducedMotion, useScroll, useTransform } from "motion/react";
import type { ImageSlot as Slot } from "@/lib/catalog";
import { ImageSlot } from "./ImageSlot";

const ease = [0.16, 1, 0.3, 1] as const;

/**
 * Every photo on the site goes through this. Three layers, each with a job:
 *  - reveal: the frame opens from an inset clip as it enters view (draws the eye in reading order)
 *  - settle: the photo eases from 1.12x to 1x inside the frame (a slow camera push)
 *  - parallax: the photo drifts against the scroll (depth)
 * Reduced motion: no clip, no zoom, no drift; a plain fade (see MotionProvider).
 */
export function MotionImage({
  slot,
  className = "",
  sizes,
  priority,
  radius = "0px",
  reveal = true,
  parallax = 6,
  delay = 0,
}: {
  slot: Slot;
  className?: string;
  sizes?: string;
  priority?: boolean;
  /** Match the container's corner radius so the clip edge lines up. */
  radius?: string;
  reveal?: boolean;
  /** Drift distance in percent of the frame height. 0 turns it off. */
  parallax?: number;
  delay?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "end start"] });
  const drift = reduce ? 0 : parallax;
  const y = useTransform(scrollYProgress, [0, 1], [`-${drift}%`, `${drift}%`]);

  return (
    <motion.div
      ref={ref}
      className={`relative overflow-hidden ${className}`}
      style={{ borderRadius: radius }}
      initial={reveal ? { opacity: 0, clipPath: `inset(12% 10% 12% 10% round ${radius})` } : { opacity: 0 }}
      whileInView={{ opacity: 1, clipPath: `inset(0% 0% 0% 0% round ${radius})` }}
      viewport={{ once: true, amount: 0.15 }}
      transition={{ duration: reduce ? 0.3 : 1.2, delay, ease }}
    >
      {/* Oversized by the drift distance so the edges never show while it moves. */}
      <motion.div className="absolute" style={{ y, inset: `-${parallax}% 0` }}>
        <motion.div
          className="h-full w-full"
          initial={{ scale: 1.12 }}
          whileInView={{ scale: 1 }}
          viewport={{ once: true, amount: 0.15 }}
          transition={{ duration: 1.8, delay, ease }}
        >
          <ImageSlot slot={slot} sizes={sizes} priority={priority} className="h-full w-full" />
        </motion.div>
      </motion.div>
    </motion.div>
  );
}
