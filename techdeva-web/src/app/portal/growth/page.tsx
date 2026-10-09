"use client";

import { ArrowRight, FlaskConical, Map, Workflow } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { ReadableValue } from "@/components/ReadableValue";
import { SessionGate } from "@/components/SessionGate";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Notice } from "@/components/ui/Notice";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/Table";
import { API_BASE } from "@/lib/api";
import { humanize, relativeTime } from "@/lib/labels";

/**
 * The client's own research and growth plan.
 *
 * AnalysisAgent and GrowthPlannerAgent already run and store their output;
 * this page reads it back. It renders what exists and names what does not,
 * rather than filling the screen with a plausible-looking plan.
 */

interface Stage {
  type: string;
  status: string;
  count: number;
  last_at: string | null;
}

interface Growth {
  analysis: unknown;
  content_plan: unknown;
  pipeline: Stage[];
  has_analysis: boolean;
  has_plan: boolean;
  missing: string[];
}

const STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  done: { label: "Done", tone: "success" },
  waiting: { label: "Waiting", tone: "neutral" },
  needs_human: { label: "Needs your approval", tone: "warning" },
  failed: { label: "Failed", tone: "danger" },
};

export default function GrowthPage() {
  return (
    <SessionGate title="Growth plan" purpose="see your growth plan">
      {() => <Growth />}
    </SessionGate>
  );
}

function Growth() {
  const [data, setData] = useState<Growth | null | undefined>(undefined);

  useEffect(() => {
    fetch(`${API_BASE}/portal/growth`, { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => setData(null));
  }, []);

  const header = (
    <PageHeader
      title="Growth plan"
      description="What your AI team has learned about your market, and what it plans to do about it."
    />
  );

  if (data === undefined) {
    return (
      <div className="flex flex-col gap-6">
        {header}
        <Skeleton className="h-48 rounded-[var(--card-radius)]" />
        <Skeleton className="h-40 rounded-[var(--card-radius)]" />
      </div>
    );
  }

  if (data === null) {
    return (
      <div className="flex flex-col gap-6">
        {header}
        <Notice tone="offline" title="Couldn't load your growth plan">
          Your connection or our server didn&apos;t answer. Nothing was changed. Reload the page to try again.
        </Notice>
      </div>
    );
  }

  const needsApproval = data.pipeline.filter((s) => s.status === "needs_human").reduce((n, s) => n + s.count, 0);

  return (
    <div className="flex max-w-4xl flex-col gap-8">
      {header}

      {needsApproval > 0 ? (
        <Notice
          tone="warning"
          title={`${needsApproval} item${needsApproval === 1 ? " is" : "s are"} waiting for your approval`}
          action={
            <ButtonLink href="/portal/approvals" size="sm" variant="secondary">
              Review <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </ButtonLink>
          }
        >
          Nothing is published or spent until you decide.
        </Notice>
      ) : null}

      <Section icon={FlaskConical} title="Market research" ready={data.has_analysis}>
        {data.has_analysis ? (
          <ReadableValue value={data.analysis} />
        ) : (
          <p className="text-[15px] text-fg-muted">
            Not run yet. Research starts once your business details and competitors are in{" "}
            <Link className="font-medium text-accent-ink underline-offset-2 hover:underline" href="/portal/onboarding">
              Set up business
            </Link>
            .
          </p>
        )}
      </Section>

      <Section icon={Map} title="Plan" ready={data.has_plan}>
        {data.has_plan ? (
          <ReadableValue value={data.content_plan} />
        ) : (
          <p className="text-[15px] text-fg-muted">Not written yet. The plan is built from the research above.</p>
        )}
      </Section>

      <section aria-labelledby="pipeline-title">
        <h2 id="pipeline-title" className="flex items-center gap-2 text-lg font-semibold text-fg">
          <Workflow className="h-5 w-5 text-accent-ink" aria-hidden="true" />
          What your AI team is doing
        </h2>
        {data.pipeline.length === 0 ? (
          <p className="mt-3 text-[15px] text-fg-muted">No work queued yet.</p>
        ) : (
          <div className="mt-4">
            <Table caption="Work in progress">
              <THead>
                <tr>
                  <TH>Work</TH>
                  <TH>State</TH>
                  <TH className="hidden sm:table-cell">Last activity</TH>
                  <TH className="text-right">Items</TH>
                </tr>
              </THead>
              <TBody>
                {data.pipeline.map((s) => {
                  const st = STATUS[s.status] ?? { label: humanize(s.status), tone: "neutral" as BadgeTone };
                  return (
                    <TR key={`${s.type}-${s.status}`}>
                      <TD className="font-medium">{humanize(s.type)}</TD>
                      <TD>
                        <Badge tone={st.tone}>{st.label}</Badge>
                      </TD>
                      <TD className="hidden text-fg-muted sm:table-cell">{relativeTime(s.last_at)}</TD>
                      <TD className="text-right tabular-nums">{s.count}</TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          </div>
        )}
      </section>
    </div>
  );
}

function Section({
  icon: Icon,
  title,
  ready,
  children,
}: {
  icon: typeof Map;
  title: string;
  ready: boolean;
  children: React.ReactNode;
}) {
  return (
    <Card as="section" className="p-5 sm:p-6">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-fg">
          <Icon className="h-5 w-5 text-accent-ink" aria-hidden="true" />
          {title}
        </h2>
        <Badge tone={ready ? "success" : "neutral"} dot>
          {ready ? "Ready" : "Not yet"}
        </Badge>
      </div>
      <div className="mt-5">{children}</div>
    </Card>
  );
}
