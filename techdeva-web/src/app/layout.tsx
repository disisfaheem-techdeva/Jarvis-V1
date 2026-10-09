import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import localFont from "next/font/local";
import Script from "next/script";

import { themeScript } from "@/components/ui/theme";
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

export const metadata: Metadata = {
  title: {
    default: "TechDeva — AI Business Operating System",
    template: "%s · TechDeva",
  },
  description: "One platform, one AI workforce, every business function on autopilot.",
};

export const viewport: Viewport = {
  themeColor: "#050408",
  viewportFit: "cover",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} ${display.variable} h-full antialiased`}
    >
      <body className="td-grain flex min-h-full flex-col">
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
