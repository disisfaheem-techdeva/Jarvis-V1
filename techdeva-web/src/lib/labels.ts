import type { BadgeTone } from "@/components/ui/Badge";

/**
 * Plain-language names for the identifiers the API returns. Unknown values
 * fall back to a humanised version of the identifier rather than being hidden.
 */

export function humanize(id: string | null | undefined): string {
  if (!id) return "";
  const s = id.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

const LEAD_STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  new: { label: "New", tone: "accent" },
  contacted: { label: "Contacted", tone: "info" },
  qualified: { label: "Qualified", tone: "info" },
  booked: { label: "Booked", tone: "success" },
  won: { label: "Won", tone: "success" },
  converted: { label: "Converted", tone: "success" },
  lost: { label: "Lost", tone: "neutral" },
};

export function leadStatus(status: string): { label: string; tone: BadgeTone } {
  return LEAD_STATUS[status?.toLowerCase()] ?? { label: humanize(status), tone: "neutral" };
}

const SOURCE: Record<string, string> = {
  whatsapp: "WhatsApp",
  meta_ads: "Meta ads",
  meta: "Meta ads",
  facebook: "Facebook",
  instagram: "Instagram",
  google: "Google",
  google_ads: "Google ads",
  website: "Website",
  walk_in: "Walk-in",
  referral: "Referral",
};

export function sourceLabel(source: string): string {
  return SOURCE[source?.toLowerCase()] ?? humanize(source);
}

const AGENT: Record<string, string> = {
  campaign_manager: "Your campaign manager",
  social_media: "Your social media expert",
  social_media_expert: "Your social media expert",
  marketing_expert: "Your marketing expert",
  designer: "Your designer",
  analyzer: "Your analyst",
  analysis: "Your analyst",
  reporting: "Reporting",
  hermes: "Hermes",
  growth_planner: "Your growth planner",
};

export function agentLabel(agent: string): string {
  return AGENT[agent?.toLowerCase()] ?? humanize(agent);
}

const KIND: Record<string, string> = {
  ad_campaign: "Ad campaign",
  campaign: "Ad campaign",
  post: "Social post",
  social_post: "Social post",
  content_plan: "Content plan",
  price_change: "Price change",
};

export function kindLabel(kind: string): string {
  return KIND[kind?.toLowerCase()] ?? humanize(kind);
}

const DECISION: Record<string, { label: string; tone: BadgeTone }> = {
  awaiting_approval: { label: "Waiting for you", tone: "warning" },
  approved: { label: "Approved", tone: "success" },
  rejected: { label: "Rejected", tone: "neutral" },
  done: { label: "Done", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

export function decisionStatus(status: string): { label: string; tone: BadgeTone } {
  return DECISION[status?.toLowerCase()] ?? { label: humanize(status), tone: "neutral" };
}

/** "3 min ago", "yesterday", "12 Oct" — short, for lists. */
export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = Math.round((now - t) / 1000);
  if (diff < 0) return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  if (diff < 172800) return "yesterday";
  if (diff < 604800) return `${Math.floor(diff / 86400)} days ago`;
  return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function clockTime(d: Date): string {
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}
