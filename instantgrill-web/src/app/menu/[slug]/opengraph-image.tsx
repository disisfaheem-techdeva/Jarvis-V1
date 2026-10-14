import { findProduct } from "@/lib/catalog";
import { ogCard, ogSize } from "@/lib/og";
import { formatPKR } from "@/lib/pricing";

export const alt = "Instant Grill BBQ box";
export const size = ogSize;
export const contentType = "image/png";

export default async function Image({ params }: { params: Promise<{ slug: string }> }) {
  const p = findProduct((await params).slug);
  if (!p) return ogCard("Instant Grill", "Ready-to-grill BBQ boxes in Lahore");
  const meta = [p.serves && `Serves ${p.serves}`, formatPKR(p.price)].filter(Boolean).join("  ·  ");
  return ogCard(p.name, `${meta}. Grill and coal included.`, "READY-TO-GRILL BBQ BOX");
}
