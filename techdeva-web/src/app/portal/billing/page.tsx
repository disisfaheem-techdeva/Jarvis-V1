import { CreditCard } from "lucide-react";

import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

// No billing/invoicing endpoint exists on the API yet. Pricing and billing are
// Founder-approval items per the Constitution: wire this page once that
// endpoint exists rather than showing invented numbers.
export default function BillingPage() {
  return (
    <div className="max-w-3xl">
      <PageHeader title="Billing" description="Your plan, invoices and payments." />
      <EmptyState
        className="mt-8"
        icon={CreditCard}
        title="Invoices will appear here"
        body="Billing isn't in the portal yet. Until it is, your invoices come directly from your TechDeva contact."
      />
    </div>
  );
}
