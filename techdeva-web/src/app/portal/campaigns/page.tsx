import { ArrowRight, Megaphone } from "lucide-react";

import { ButtonLink } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

// Ad campaigns always park for approval before spend goes live — enforced in
// the platform's policy layer, not just this UI. A read/approve view should
// wire to the orchestrator's task queue for campaign-type tasks once that
// endpoint is confirmed. Until then, campaign approvals live on /portal/approvals.
export default function CampaignsPage() {
  return (
    <div className="max-w-3xl">
      <PageHeader title="Campaigns" description="The ads your AI team runs for you, and how each one is doing." />
      <EmptyState
        className="mt-8"
        icon={Megaphone}
        title="Your campaigns will be listed here"
        body="Every new campaign already stops for your approval before any money is spent. You'll find those requests in Approvals today; a full campaign history is coming to this page."
        action={
          <ButtonLink href="/portal/approvals" variant="secondary">
            Go to approvals <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </ButtonLink>
        }
      />
    </div>
  );
}
