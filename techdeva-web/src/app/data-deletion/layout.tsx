import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Delete your data",
  description: "How to ask TechDeva to delete your business data and your Facebook, Instagram and Google connections.",
  alternates: { canonical: "/data-deletion" },
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
