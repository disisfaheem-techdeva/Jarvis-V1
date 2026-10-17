import { adminConfigured, isAdminSession } from "@/lib/auth";
import { AdminLogin } from "./AdminLogin";
import { AdminNav } from "./AdminNav";

/** Renders the login form instead of the page unless there is a valid owner session. */
export async function AdminPage({ title, children }: { title: string; children: () => Promise<React.ReactNode> | React.ReactNode }) {
  if (!(await isAdminSession())) return <AdminLogin configured={adminConfigured()} />;
  return (
    <>
      <AdminNav />
      <h1 className="mb-8 mt-6 text-3xl font-semibold tracking-[-0.03em] text-ash-50 print:hidden">{title}</h1>
      {await children()}
    </>
  );
}
