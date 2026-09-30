import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Set your password",
  description: "Set the password for your TechDeva login.",
  alternates: { canonical: "/set-password" },
  robots: { index: false, follow: false },
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
