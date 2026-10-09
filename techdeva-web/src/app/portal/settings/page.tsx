import { ArrowRight, Settings } from "lucide-react";

import { ButtonLink } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

// Business config (services, price list, hours, persona) is written during
// onboarding and read by the agents directly. `save_config` exists in
// persistence but isn't exposed as an API route yet; wire this page to it once
// a tenant self-service endpoint exists.
export default function SettingsPage() {
  return (
    <div className="max-w-3xl">
      <PageHeader title="Settings" description="How your AI team represents your business." />
      <EmptyState
        className="mt-8"
        icon={Settings}
        title="Your services, prices and hours live in Set up business"
        body="Your AI team reads everything you enter there. More settings, like how Hermes greets your clients, will appear on this page later. Until then, ask your TechDeva contact to change them."
        action={
          <ButtonLink href="/portal/onboarding" variant="secondary">
            Open Set up business <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </ButtonLink>
        }
      />
    </div>
  );
}
