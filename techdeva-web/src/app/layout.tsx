import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import localFont from "next/font/local";
import Script from "next/script";

import { JsonLd } from "@/components/JsonLd";
import { themeScript } from "@/components/ui/theme";
import { ORG, SITE_NAME, SITE_URL } from "@/lib/site";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

// Display face: headlines and headline metrics only. Self-hosted (OFL) so the
// build doesn't depend on fetching it; one variable file covers 500–700.
const display = localFont({
  src: "./fonts/BricolageGrotesque-latin.woff2",
  variable: "--font-display-face",
  weight: "500 700",
  display: "swap",
});

export const viewport: Viewport = {
  themeColor: "#050408",
  viewportFit: "cover",
};

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "TechDeva: clinic & salon software with a WhatsApp AI agent",
    template: "%s | TechDeva",
  },
  description:
    "One dashboard for clinics, salons and academies: bookings, bills, customers, stock, team, a WhatsApp AI agent that " +
    "answers and books 24/7, Facebook & Instagram posting, ad results and a growth advisor. 7-day free trial.",
  applicationName: "TechDeva",
  keywords: [
    "clinic management software Pakistan", "salon software Pakistan", "WhatsApp AI agent", "WhatsApp chatbot for clinics",
    "appointment booking software", "aesthetic clinic CRM", "dental clinic software", "AI receptionist",
    "Facebook ads automation", "business dashboard", "TechDeva",
  ],
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    siteName: SITE_NAME,
    locale: "en_PK",
    url: "/",
    title: "TechDeva: clinic & salon software with a WhatsApp AI agent",
    description: "Bookings, bills, customers, stock, team and a WhatsApp AI agent in one dashboard. 7-day free trial.",
  },
  twitter: {
    card: "summary",
    title: "TechDeva: clinic & salon software with a WhatsApp AI agent",
    description: "Bookings, bills, customers, stock, team and a WhatsApp AI agent in one dashboard.",
  },
  robots: { index: true, follow: true },
};

const ORG_LD = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": `${SITE_URL}/#org`,
      name: ORG.name,
      alternateName: "TechDeva",
      url: ORG.url,
      address: { "@type": "PostalAddress", addressLocality: ORG.city, addressCountry: ORG.country },
    },
    {
      "@type": "SoftwareApplication",
      "@id": `${SITE_URL}/#app`,
      name: "TechDeva AIBOS",
      applicationCategory: "BusinessApplication",
      operatingSystem: "Web, Android, iOS (browser)",
      url: SITE_URL,
      publisher: { "@id": `${SITE_URL}/#org` },
      description:
        "Business operating system for clinics, salons, gyms and academies: appointments, invoices, customers, stock, " +
        "team and HR, a WhatsApp AI sales agent, Facebook and Instagram posting, live Meta and Google ad results, " +
        "a growth advisor and an ad autopilot.",
      offers: [
        { "@type": "Offer", name: "Starter", price: "19999", priceCurrency: "PKR" },
        { "@type": "Offer", name: "Growth", price: "49999", priceCurrency: "PKR" },
        { "@type": "Offer", name: "Pro", price: "64999", priceCurrency: "PKR" },
      ],
    },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} ${display.variable} h-full antialiased`}
    >
      <body className="td-grain flex min-h-full flex-col">
        <JsonLd data={ORG_LD} />
        <a
          href="#main"
          className="sr-only z-[70] rounded-[var(--btn-radius)] bg-accent px-4 py-3 text-sm font-medium text-on-accent focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
        >
          Skip to content
        </a>
        {children}
        <Script id="td-theme" strategy="beforeInteractive">
          {themeScript}
        </Script>
      </body>
    </html>
  );
}
