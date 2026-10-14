import { product } from "@/lib/catalog";
import { formatPKR } from "@/lib/pricing";
import { ButtonLink } from "../ui";

/** Thumb-reach order button on phones, where most of this traffic comes from. */
export function MobileBuyBar() {
  const from = Math.min(...product.bundles.map((b) => b.price));
  return (
    <div className="fixed inset-x-0 bottom-0 z-30 border-t border-white/[0.08] bg-coal-900/90 px-4 pb-[calc(env(safe-area-inset-bottom)+12px)] pt-3 backdrop-blur-xl md:hidden">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-xs text-ash-400">From</p>
          <p className="font-mono font-semibold text-ash-50">{formatPKR(from)}</p>
        </div>
        <ButtonLink href="#shop" className="flex-1">
          Order now
        </ButtonLink>
      </div>
    </div>
  );
}
