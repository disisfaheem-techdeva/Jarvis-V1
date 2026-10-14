"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { images, steps } from "@/lib/catalog";
import { ImageSlot } from "../ImageSlot";
import { MotionImage } from "../MotionImage";
import { Container } from "../ui";

/**
 * Desktop: the photo column stays pinned while the steps scroll past; the
 * photo swaps to whichever step is in view. Mobile: each step carries its own
 * photo, stacked.
 */
export function HowItWorks() {
  const [active, setActive] = useState(0);

  return (
    <section id="how" className="scroll-mt-16 border-t border-white/[0.06] bg-coal-900 py-24 md:py-32">
      <Container>
        <h2 className="text-4xl font-semibold tracking-[-0.03em] text-ash-50 md:text-5xl">
          From bag of coal to dinner
        </h2>

        <div className="mt-14 grid gap-12 md:grid-cols-2 md:gap-16">
          <div className="relative hidden md:block">
            <div className="sticky top-28">
              <div className="relative aspect-[4/3] overflow-hidden rounded-[var(--radius-card)] ring-1 ring-white/[0.06]">
                <AnimatePresence mode="popLayout" initial={false}>
                  <motion.div
                    key={active}
                    className="absolute inset-0"
                    initial={{ opacity: 0, scale: 1.12, clipPath: "inset(0% 0% 100% 0%)" }}
                    animate={{ opacity: 1, scale: 1, clipPath: "inset(0% 0% 0% 0%)" }}
                    exit={{ opacity: 0, scale: 0.98 }}
                    transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
                  >
                    <ImageSlot slot={images[steps[active].image]} sizes="50vw" className="h-full w-full" />
                  </motion.div>
                </AnimatePresence>
              </div>
              <div className="mt-5 flex gap-2" aria-hidden>
                {steps.map((s, i) => (
                  <span
                    key={s.title}
                    className={`h-1 flex-1 rounded-full transition-colors duration-500 ${
                      i <= active ? "bg-ember-500" : "bg-coal-700"
                    }`}
                  />
                ))}
              </div>
            </div>
          </div>

          <ol className="space-y-6 md:space-y-0">
            {steps.map((s, i) => (
              <motion.li
                key={s.title}
                onViewportEnter={() => setActive(i)}
                viewport={{ amount: 0.6 }}
                className="flex flex-col justify-center md:min-h-[70vh]"
              >
                <MotionImage
                  slot={images[s.image]}
                  sizes="100vw"
                  radius="20px"
                  className="mb-6 aspect-[4/3] md:hidden"
                />
                <span className="font-mono text-sm text-ember-400">{i + 1}</span>
                <h3
                  className={`mt-2 text-3xl font-semibold tracking-[-0.03em] transition-colors duration-500 md:text-5xl ${
                    active === i ? "text-ash-50" : "text-ash-50 md:text-coal-600"
                  }`}
                >
                  {s.title}
                </h3>
                <p className="mt-3 max-w-sm text-lg leading-relaxed text-ash-300">{s.body}</p>
              </motion.li>
            ))}
          </ol>
        </div>
      </Container>
    </section>
  );
}
