import { Building2 } from "lucide-react";

import { ConsoleHeader } from "@/components/FounderBits";
import { EmptyState } from "@/components/ui/EmptyState";

export default function TenantsPage() {
  return (
    <div className="flex flex-col gap-8">
      <ConsoleHeader title="Tenants" description="Every business on the platform, with its status." />
      <EmptyState
        icon={Building2}
        title="No per-tenant list endpoint yet"
        body={
          <>
            <code className="rounded bg-surface-3 px-1.5 py-0.5 text-xs">/techdeva/overview</code> returns aggregate counts only.
            Add a <code className="rounded bg-surface-3 px-1.5 py-0.5 text-xs">/techdeva/tenants</code> route that returns id, name and
            status per tenant, then wire this page to it — don&apos;t fabricate a tenant list here.
          </>
        }
      />
    </div>
  );
}
