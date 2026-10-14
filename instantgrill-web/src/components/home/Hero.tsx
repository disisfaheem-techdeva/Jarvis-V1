"use client";

import { useRef } from "react";
import { motion, useReducedMotion, useScroll, useTransform } from "motion/react";
import { WhatsappLogo } from "@phosphor-icons/react";
import { brand, images } from "@/lib/catalog";
import { MotionImage } from "../MotionImage";
import { ButtonLink, Container, buttonClass } from "../ui";
import { whatsappLink } from "../WhatsAppFab";

const ease = [0.16, 1, 0.3, 1] as const;

export function Hero() {
  const ref = useRef<HTMLElement>(null);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end start"] });
  // The product drifts slower than the page as you scroll away: depth, not decoration.
  const y = useTransform(scrollYProgress, [0, 1], [0, reduce ? 0 : 90]);
  const scale = useTransform(scrollYProgress, [0, 1], [1, reduce ? 1 : 1.06]);

  const enter = (delay: number) => ({
    initial: { opacity: 0, y: 24 },
    animate: { opacity: 1, y: 0 },
    transition: { duration: 0.8, delay, ease },
  });

  return (
    <section ref={ref} className="relative overflow-hidden">
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-[70%] bg-[radial-gradient(50%_60%_at_75%_80%,rgb(242_101_42/0.18),transparent_70%)]" />
      <Container className="relative grid min-h-[calc(100dvh-4rem)] items-center gap-10 py-12 md:grid-cols-[1.05fr_1fr] md:py-16 lg:gap-16">
        <div className="max-w-xl">
          <motion.p {...enter(0)} className="text-sm font-semibold uppercase tracking-[0.14em] text-ember-300">
            Pakistan&apos;s first
          </motion.p>
          <motion.h1
            {...enter(0.08)}
            className="mt-5 text-5xl font-semibold leading-[1.02] tracking-[-0.035em] text-ash-50 sm:text-6xl lg:text-7xl"
          >
            Ready-to-Grill <span className="text-ash-400">BBQ Box</span>
          </motion.h1>
          <motion.p {...enter(0.16)} className="mt-6 max-w-md text-lg leading-relaxed text-ash-300">
            Grill, coal, marinated meat, pita and sides in one box. Delivered across Lahore.
          </motion.p>
          <motion.div {...enter(0.24)} className="mt-9 flex flex-wrap gap-3">
            <ButtonLink href="/menu" size="lg">
              Build your box
            </ButtonLink>
            <a
              href={whatsappLink(`Assalam o Alaikum ${brand.name}, I want to order a BBQ box.`)}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonClass("ghost", "lg")}
            >
              <WhatsappLogo size={18} weight="fill" className="text-[#25d366]" /> Order on WhatsApp
            </a>
          </motion.div>
        </div>

        <motion.div
          style={{ y, scale }}
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 1.1, ease }}
          className="relative mx-auto w-full max-w-[520px]"
        >
          <div className="ember-glow ember-flicker absolute -inset-10 blur-2xl" />
          <div className="relative rounded-[var(--radius-card)] ring-1 ring-white/[0.08]">
            <MotionImage
              slot={images.hero}
              priority
              radius="20px"
              parallax={0}
              delay={0.15}
              sizes="(min-width: 768px) 45vw, 100vw"
              className="aspect-[4/5]"
            />
          </div>
        </motion.div>
      </Container>
    </section>
  );
}
