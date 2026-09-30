import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

const PAGES: [string, number, MetadataRoute.Sitemap[number]["changeFrequency"]][] = [
  ["/", 1, "weekly"],
  ["/pricing", 0.9, "monthly"],
  ["/platform", 0.8, "monthly"],
  ["/solutions", 0.7, "monthly"],
  ["/ai-workforce", 0.6, "monthly"],
  ["/case-studies", 0.6, "monthly"],
  ["/demo", 0.6, "yearly"],
  ["/privacy", 0.2, "yearly"],
  ["/terms", 0.2, "yearly"],
  ["/data-deletion", 0.2, "yearly"],
];

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  return PAGES.map(([path, priority, changeFrequency]) => ({
    url: `${SITE_URL}${path === "/" ? "" : path}`,
    lastModified: now,
    changeFrequency,
    priority,
  }));
}
