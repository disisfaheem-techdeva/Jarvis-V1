import { API_BASE } from "@/lib/api";

// The self-serve signup lives on the API host: it creates the business, the
// owner's login and a 14-day trial, then opens onboarding.
export const SIGNUP_URL = `${API_BASE}/`;
export const DASHBOARD_URL = `${API_BASE}/dashboard`;
export const TRIAL_DAYS = 14;

export const NAV = [
  { href: "/platform", label: "Platform" },
  { href: "/ai-workforce", label: "AI Workforce" },
  { href: "/solutions", label: "Solutions" },
  { href: "/pricing", label: "Pricing" },
  { href: "/case-studies", label: "Case Studies" },
];
