import type { Metadata, Viewport } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { MotionProvider } from "@/components/MotionProvider";
import { CartProvider } from "@/components/cart/CartProvider";
import { MenuProvider } from "@/components/menu/MenuProvider";
import { getMenu } from "@/lib/menu";
import { CartDrawer } from "@/components/cart/CartDrawer";
import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";
import { WhatsAppFab } from "@/components/WhatsAppFab";
import { Analytics } from "@/components/Analytics";
import { brand } from "@/lib/catalog";
import "./globals.css";

const siteUrl = `https://${brand.domain}`;

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: `${brand.name} | Ready-to-grill BBQ box delivered in Lahore`,
    template: `%s | ${brand.name}`,
  },
  description:
    "Pakistan's first ready-to-grill BBQ box. Grill, coal, marinated meat, pita and sides in one box, delivered across Lahore. Cash on delivery.",
  openGraph: {
    type: "website",
    siteName: brand.name,
    url: siteUrl,
    locale: "en_PK",
    description: "Pakistan's first ready-to-grill BBQ box, delivered across Lahore.",
  },
  twitter: { card: "summary_large_image" },
  keywords: [
    "BBQ box Lahore",
    "ready to grill BBQ",
    "BBQ delivery Lahore",
    "BBQ ghar pe",
    "tikka boti delivery Lahore",
    "seekh kabab box",
    "disposable grill BBQ Pakistan",
    "family BBQ deal Lahore",
    "dawat BBQ Lahore",
    "Instant Grill",
  ],
  alternates: { canonical: "/" },
};

export const viewport: Viewport = {
  themeColor: "#0c0d0f",
  colorScheme: "dark",
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const menu = await getMenu();
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="grain min-h-[100dvh] antialiased">
        <MotionProvider>
          <MenuProvider menu={menu}>
          <CartProvider>
            <a
              href="#main"
              className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[70] focus:rounded-full focus:bg-ember-500 focus:px-4 focus:py-2 focus:text-coal-950"
            >
              Skip to content
            </a>
            <Header />
            <main id="main">{children}</main>
            <Footer />
            <CartDrawer />
            <WhatsAppFab />
          </CartProvider>
          </MenuProvider>
        </MotionProvider>
        <Analytics />
      </body>
    </html>
  );
}
