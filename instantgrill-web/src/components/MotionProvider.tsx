"use client";

import { MotionConfig } from "motion/react";

/**
 * reducedMotion="user": for visitors who ask for less motion, Motion drops
 * transform animations (slides, scale, parallax) and keeps opacity fades.
 * Components must render the same initial props on server and client, so they
 * never branch on useReducedMotion for `initial`; this config handles it.
 */
export function MotionProvider({ children }: { children: React.ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}
