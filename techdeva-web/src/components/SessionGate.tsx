"use client";

import { LogIn } from "lucide-react";

import { Button, ButtonLink } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Notice } from "@/components/ui/Notice";
import { LoadingRegion, Skeleton } from "@/components/ui/Skeleton";
import { useSession, type Session } from "@/lib/useTenant";

/**
 * Renders children only for a signed-in session, and a distinct, truthful
 * screen otherwise: loading, signed out, or "couldn't reach TechDeva".
 */
export function SessionGate({
  title,
  purpose,
  children,
  skeleton,
}: {
  /** Page title, kept as the h1 in every state. */
  title: string;
  /** What signing in lets you do here, e.g. "review what is waiting". */
  purpose: string;
  children: (session: Session) => React.ReactNode;
  skeleton?: React.ReactNode;
}) {
  const { session, status, retry } = useSession();

  if (status === "loading") {
    return (
      <LoadingRegion label={`Loading ${title}`}>
        <h1 className="sr-only">{title}</h1>
        {skeleton ?? <DefaultSkeleton />}
      </LoadingRegion>
    );
  }

  if (status === "unreachable") {
    return (
      <div className="max-w-2xl">
        <h1 className="font-display text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{title}</h1>
        <Notice
          tone="offline"
          title="Couldn't reach TechDeva"
          className="mt-6"
          action={
            <Button variant="secondary" size="sm" onClick={retry}>
              Try again
            </Button>
          }
        >
          Your connection or our server didn&apos;t answer. Nothing was changed. If this keeps
          happening, message your TechDeva contact.
        </Notice>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="max-w-2xl">
        <h1 className="font-display text-2xl font-semibold tracking-tight text-fg sm:text-3xl">{title}</h1>
        <EmptyState
          className="mt-6"
          icon={LogIn}
          title="You're signed out"
          body={`Sign in to ${purpose}.`}
          action={<ButtonLink href="/login">Sign in</ButtonLink>}
        />
      </div>
    );
  }

  return <>{children(session)}</>;
}

function DefaultSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-9 w-56" />
      <Skeleton className="h-5 w-80 max-w-full" />
      <div className="grid gap-4 sm:grid-cols-2">
        <Skeleton className="h-32" />
        <Skeleton className="h-32" />
      </div>
    </div>
  );
}
