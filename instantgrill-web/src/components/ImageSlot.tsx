import Image from "next/image";
import { Camera } from "@phosphor-icons/react/dist/ssr";
import type { ImageSlot as Slot } from "@/lib/catalog";

/**
 * Renders a catalog image, or a clearly labelled placeholder until the real
 * photo exists. Set `src` in lib/catalog.ts once the file is in /public/images.
 * The placeholder is deliberately obvious so nobody ships it by accident.
 */
export function ImageSlot({
  slot,
  className = "",
  sizes = "(min-width: 1024px) 50vw, 100vw",
  priority = false,
}: {
  slot: Slot;
  className?: string;
  sizes?: string;
  priority?: boolean;
}) {
  if (slot.src) {
    return (
      <div className={`relative overflow-hidden ${className}`}>
        <Image src={slot.src} alt={slot.alt} fill sizes={sizes} priority={priority} className="object-cover" />
      </div>
    );
  }
  return (
    <div
      role="img"
      aria-label={`${slot.alt} (photo coming soon)`}
      className={`relative overflow-hidden bg-coal-850 ${className}`}
    >
      <div className="ember-glow absolute inset-0 opacity-70" />
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_20%,rgb(255_255_255/0.06),transparent_50%)]" />
      {process.env.NODE_ENV !== "production" && (
        <div className="absolute inset-x-4 top-4 flex items-start gap-2 rounded-[var(--radius-field)] bg-coal-950/70 p-3 text-xs leading-snug text-ash-300 backdrop-blur">
          <Camera size={16} weight="bold" className="mt-0.5 shrink-0 text-ember-400" />
          <span>{slot.brief}</span>
        </div>
      )}
    </div>
  );
}
