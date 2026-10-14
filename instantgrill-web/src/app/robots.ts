import type { MetadataRoute } from "next";
import { brand } from "@/lib/catalog";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: ["/admin", "/api", "/checkout", "/order"] }],
    sitemap: `https://${brand.domain}/sitemap.xml`,
  };
}
