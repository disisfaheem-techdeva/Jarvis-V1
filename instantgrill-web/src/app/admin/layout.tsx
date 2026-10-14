import type { Metadata } from "next";
import { Container } from "@/components/ui";

export const metadata: Metadata = { title: "Admin", robots: { index: false, follow: false } };

/** Each admin page checks the session itself; a layout alone is not an auth boundary. */
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <Container className="py-8 md:py-12">{children}</Container>;
}
