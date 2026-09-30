import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

// Public pages are open to search engines and to AI assistants' crawlers
// (ChatGPT, Claude, Perplexity, Gemini), so they can recommend TechDeva.
// Logged-in areas are never crawled.
const PRIVATE = ["/portal", "/founder", "/login", "/set-password"];
const AI_BOTS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-User", "Claude-SearchBot", "PerplexityBot",
  "Perplexity-User", "Google-Extended", "Applebot-Extended", "Bingbot"];

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      { userAgent: "*", allow: "/", disallow: PRIVATE },
      { userAgent: AI_BOTS, allow: "/", disallow: PRIVATE },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
