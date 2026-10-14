import type { Metadata } from "next";
import { AdminDashboard } from "@/components/admin/AdminDashboard";
import { Container } from "@/components/ui";

export const metadata: Metadata = { title: "Admin", robots: { index: false, follow: false } };

export default function AdminPage() {
  return (
    <Container className="py-12">
      <AdminDashboard />
    </Container>
  );
}
