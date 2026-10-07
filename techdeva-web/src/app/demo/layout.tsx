import type { Metadata } from "next";

export const metadata: Metadata = { title: "Book a demo" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
