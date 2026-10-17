import { ogCard, ogSize } from "@/lib/og";

export const alt = "Instant Grill: ready-to-grill BBQ box delivered in Lahore";
export const size = ogSize;
export const contentType = "image/png";

export default function Image() {
  return ogCard("Ready-to-Grill BBQ Box", "Grill, coal, marinated meat, pita and sides. Delivered across Lahore.");
}
