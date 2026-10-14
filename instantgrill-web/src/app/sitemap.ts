import type { MetadataRoute } from "next";
import { brand, products } from "@/lib/catalog";

export default function sitemap(): MetadataRoute.Sitemap {
  const base = `https://${brand.domain}`;
  const pages = ["", "/menu", "/track", "/policies"].map((p) => ({
    url: `${base}${p}`,
    changeFrequency: "weekly" as const,
    priority: p === "" ? 1 : p === "/menu" ? 0.9 : 0.5,
  }));
  const items = products.map((p) => ({ url: `${base}/menu/${p.slug}`, changeFrequency: "weekly" as const, priority: 0.7 }));
  return [...pages, ...items];
}
