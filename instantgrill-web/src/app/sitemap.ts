import type { MetadataRoute } from "next";
import { brand } from "@/lib/catalog";

export default function sitemap(): MetadataRoute.Sitemap {
  const base = `https://${brand.domain}`;
  return ["", "/track", "/policies"].map((p) => ({ url: `${base}${p}`, changeFrequency: "weekly", priority: p ? 0.5 : 1 }));
}
