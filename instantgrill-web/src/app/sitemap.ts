import type { MetadataRoute } from "next";
import { brand, products } from "@/lib/catalog";

export default function sitemap(): MetadataRoute.Sitemap {
  const base = `https://${brand.domain}`;
  const weight: Record<string, number> = { "": 1, "/menu": 0.9, "/deals": 0.8, "/how-it-works": 0.6, "/faq": 0.6, "/contact": 0.6, "/about": 0.5 };
  const pages = ["", "/menu", "/deals", "/how-it-works", "/about", "/faq", "/contact", "/track", "/policies", "/privacy", "/terms"].map((p) => ({
    url: `${base}${p}`,
    changeFrequency: "weekly" as const,
    priority: weight[p] ?? 0.3,
  }));
  return [...pages, ...products.map((p) => ({ url: `${base}/menu/${p.slug}`, changeFrequency: "weekly" as const, priority: 0.7 }))];
}
