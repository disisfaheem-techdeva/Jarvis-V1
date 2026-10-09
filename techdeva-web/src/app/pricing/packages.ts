// The three packages and the tools each one replaces (Faheem, 30 Sep 2026).
// Tool prices are 2026 monthly list prices in USD from review sites; rupee
// amounts are converted at USD_TO_PKR. TechDeva's own prices come live from
// the founder console (/plans); these numbers are only the fallback.
export type Currency = "PKR" | "USD";
export const USD_TO_PKR = 277;

export type Package = {
  key: "starter" | "growth" | "pro";
  name: string;
  tagline: string;
  pkr: number;
  usd: number;
  numbers: number;
  chats: number;
  popular?: boolean;
};

export const PACKAGES: readonly Package[] = [
  { key: "starter", name: "Starter", tagline: "Run the business and let AI answer WhatsApp", pkr: 19999, usd: 99, numbers: 1, chats: 300 },
  { key: "growth", name: "Growth", tagline: "AI sells, posts, tracks your ads, and Jarvis", pkr: 49999, usd: 199, numbers: 2, chats: 1500, popular: true },
  { key: "pro", name: "Pro", tagline: "A full AI team: growth advisor and ads on autopilot", pkr: 64999, usd: 299, numbers: 5, chats: 4000 },
];

type Tier = readonly [string, number] | null;
export const ROWS: { name: string; tiers: readonly [Tier, Tier, Tier] }[] = [
  { name: "WhatsApp AI sales agent, 24/7", tiers: [["WATI Growth", 69], ["respond.io Growth", 159], ["respond.io Advanced", 279]] },
  { name: "Bookings, calendar & customer CRM", tiers: [["Vagaro, Fresha", 50], ["Vagaro, Fresha", 50], ["Vagaro, Fresha", 50]] },
  { name: "Invoices, payments & profit/loss", tiers: [["Zoho Books", 20], ["Zoho Books", 20], ["Zoho Books", 20]] },
  { name: "Stock & low-stock alerts", tiers: [["Zoho Inventory", 39], ["Zoho Inventory", 39], ["Zoho Inventory", 39]] },
  { name: "Team logins, attendance & HR", tiers: [["Connecteam", 35], ["Connecteam", 35], ["Connecteam", 35]] },
  { name: "Facebook & Instagram posting", tiers: [null, ["Metricool, Buffer", 25], ["Metricool, Buffer", 25]] },
  { name: "Live ad results, Meta & Google", tiers: [null, ["DashThis", 49], ["DashThis", 49]] },
  { name: "Jarvis, your AI business assistant", tiers: [null, ["ChatGPT Plus", 20], ["ChatGPT Plus", 20]] },
  { name: "Growth advisor & ad autopilot", tiers: [null, null, ["Bïrch (Revealbot) Pro", 99]] },
];
