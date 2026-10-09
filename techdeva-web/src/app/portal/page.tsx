"use client";

import { ArrowRight, ArrowUpRight, Hand, Inbox, Users } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { SessionGate } from "@/components/SessionGate";
import { Badge } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { MetricCard } from "@/components/MetricCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { Notice } from "@/components/ui/Notice";
import { Skeleton } from "@/components/ui/Skeleton";
import { API_BASE, getAttributionReport, getLeads, type AttributionReport, type Lead } from "@/lib/api";
import { agentLabel, clockTime, kindLabel, leadStatus, relativeTime, sourceLabel } from "@/lib/labels";
import type { Session } from "@/lib/useTenant";

interface Deliverable {
  id: string;
  agent: string;
  kind: string;
  title: string;
  status: string;
  created_at?: string;
}

export default function PortalOverview() {
  return (
    <SessionGate title="Overview" purpose="see how your business is doing" skeleton={<OverviewSkeleton />}>
      {(session) => <Overview session={session} />}
    </SessionGate>
  );
}

function Overview({ session }: { session: Session }) {
  const tenantId = session.tenant_id;
  const [report, setReport] = useState<AttributionReport | null>(null);
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [queue, setQueue] = useState<Deliverable[] | null | undefined>(undefined);
  const [errors, setErrors] = useState<string[]>([]);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    const fail = (what: string) => setErrors((e) => (e.includes(what) ? e : [...e, what]));
    Promise.all([
      getAttributionReport(tenantId).then((r) => (r.ok ? setReport(r.data) : fail("ad results"))),
      getLeads(tenantId).then((r) => (r.ok ? setLeads(r.data) : fail("leads"))),
      fetch(`${API_BASE}/portal/deliverables`, { credentials: "include", cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: { deliverables?: Deliverable[] } | null) => setQueue(d?.deliverables ?? null))
        .catch(() => setQueue(null)),
    ]).finally(() => setLoadedAt(new Date()));
  }, [tenantId]);

  if (!tenantId) {
    return (
      <EmptyState
        icon={Inbox}
        title="Your login isn't linked to a business yet"
        body="Once TechDeva links your account to your business, your leads, approvals and results appear here. Message your TechDeva contact if this takes longer than a day."
      />
    );
  }

  const now = loadedAt?.getTime() ?? 0;
  const openLeads = leads ? leads.filter((l) => l.status !== "lost").length : null;
  const newLeads = leads ? leads.filter((l) => now - new Date(l.created_at).getTime() < 86_400_000).length : null;
  const pending = queue ? queue.filter((d) => d.status === "awaiting_approval") : null;
  const recentLeads = leads
    ? [...leads].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()).slice(0, 5)
    : null;

  return (
    <div className="flex flex-col gap-8">
      <Hero pending={pending} newLeads={newLeads} report={report} loading={!loadedAt} />

      {errors.length > 0 ? (
        <Notice tone="offline" title={`Couldn't load your ${errors.join(" and ")}`}>
          Those figures show as a dash below. Nothing was changed. Reload the page to try again.
        </Notice>
      ) : null}

      <section aria-labelledby="numbers-title">
        <h2 id="numbers-title" className="sr-only">
          Your numbers
        </h2>
        <ul className="td-stagger grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
          <MetricCard i={0} label="Open leads" value={openLeads} help="People who haven't been marked lost" loading={!loadedAt} />
          <MetricCard i={1} label="New in the last 24 hours" value={newLeads} help="Leads your AI team captured today" loading={!loadedAt} />
          <MetricCard
            i={2}
            label="Cost per lead"
            value={report?.cpl ?? null}
            help="Ad spend ÷ leads from ads"
            empty="No ad spend recorded yet"
            loading={!loadedAt}
          />
          <MetricCard
            i={3}
            label="Return on ad spend"
            value={report?.roas ?? null}
            suffix="×"
            decimals={1}
            help="Revenue ÷ ad spend"
            empty="Needs both spend and revenue"
            loading={!loadedAt}
          />
        </ul>
        <p className="mt-3 text-xs text-fg-subtle">
          {loadedAt ? `Loaded ${clockTime(loadedAt)} from your TechDeva account. ` : null}A dash means nothing was
          recorded — we never show a made-up zero.
          {report?.spend != null ? ` Ad spend so far: ${report.spend.toLocaleString()}.` : null}
        </p>
      </section>

      <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        <WaitingCard pending={pending} />
        <LeadsCard leads={recentLeads} loaded={!!loadedAt} />
      </div>
    </div>
  );
}

function greeting(d = new Date()) {
  const h = d.getHours();
  if (h < 5) return "Working late";
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

function Hero({
  pending,
  newLeads,
  report,
  loading,
}: {
  pending: Deliverable[] | null;
  newLeads: number | null;
  report: AttributionReport | null;
  loading: boolean;
}) {
  const parts: string[] = [];
  if (pending)
    parts.push(
      pending.length === 0
        ? "nothing is waiting on you"
        : `${pending.length} decision${pending.length === 1 ? " is" : "s are"} waiting on you`,
    );
  if (newLeads != null) parts.push(`${newLeads} new lead${newLeads === 1 ? "" : "s"} came in since yesterday`);
  if (report?.roas != null) parts.push(`your ads are returning ${report.roas.toFixed(1)}× what they cost`);
  const joined =
    parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : (parts[0] ?? "");
  const summary = joined
    ? joined.charAt(0).toUpperCase() + joined.slice(1) + "."
    : "We couldn't read your latest numbers just now.";

  return (
    <section
      aria-labelledby="hero-title"
      className="td-glass td-rim relative overflow-hidden rounded-[var(--p-radius-xl)] px-6 py-8 sm:px-10 sm:py-12"
    >
      <div aria-hidden="true" className="td-ambient pointer-events-none absolute inset-0" />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-[radial-gradient(closest-side,var(--accent-glow),transparent)] blur-2xl"
      />
      <div className="td-stagger relative flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-2xl">
          <p style={{ "--i": 0 } as React.CSSProperties} className="text-label font-medium uppercase text-accent-ink">
            {new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}
          </p>
          <h1
            id="hero-title"
            style={{ "--i": 1 } as React.CSSProperties}
            className="mt-3 font-display text-4xl font-semibold tracking-[-0.03em] text-fg sm:text-5xl"
          >
            {greeting()}.
          </h1>
          {loading ? (
            <Skeleton className="mt-4 h-6 w-80 max-w-full" />
          ) : (
            <p style={{ "--i": 2 } as React.CSSProperties} className="mt-4 text-lg leading-relaxed text-fg-muted">
              {summary}
            </p>
          )}
        </div>
        {pending && pending.length > 0 ? (
          <div style={{ "--i": 3 } as React.CSSProperties}>
            <ButtonLink href="/portal/approvals" size="lg">
              Review {pending.length} approval{pending.length === 1 ? "" : "s"} <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </ButtonLink>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function WaitingCard({ pending }: { pending: Deliverable[] | null | undefined }) {
  return (
    <Card as="section" aria-labelledby="waiting-title" className="flex flex-col">
      <div className="flex items-center justify-between gap-4 border-b border-line px-5 py-4 sm:px-6">
        <h2 id="waiting-title" className="flex items-center gap-2 text-base font-semibold text-fg">
          <Hand className="h-4 w-4 text-warning" aria-hidden="true" />
          Waiting for you
        </h2>
        <Link href="/portal/approvals" className="inline-flex min-h-11 shrink-0 items-center gap-1 whitespace-nowrap text-sm font-medium text-accent-ink hover:underline">
          All approvals <ArrowUpRight className="h-4 w-4" aria-hidden="true" />
        </Link>
      </div>
      {pending === undefined ? (
        <div className="space-y-3 p-5 sm:p-6">
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
        </div>
      ) : pending === null ? (
        <p className="p-5 text-sm text-fg-muted sm:p-6">Couldn&apos;t load your approval queue.</p>
      ) : pending.length === 0 ? (
        <p className="p-5 text-sm text-fg-muted sm:p-6">
          Nothing is waiting on you. Your AI team will stop here before anything is published or costs money.
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {pending.slice(0, 4).map((d) => (
            <li key={d.id}>
              <Link href="/portal/approvals" className="flex items-center gap-4 px-5 py-4 transition-colors hover:bg-surface-2 sm:px-6">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[15px] font-medium text-fg">{d.title}</p>
                  <p className="mt-0.5 text-xs text-fg-subtle">
                    {agentLabel(d.agent)} · {kindLabel(d.kind)} · {relativeTime(d.created_at)}
                  </p>
                </div>
                <Badge tone="warning" dot>
                  Waiting
                </Badge>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function LeadsCard({ leads, loaded }: { leads: Lead[] | null; loaded: boolean }) {
  return (
    <Card as="section" aria-labelledby="leads-title" className="flex flex-col">
      <div className="flex items-center justify-between gap-4 border-b border-line px-5 py-4 sm:px-6">
        <h2 id="leads-title" className="flex items-center gap-2 text-base font-semibold text-fg">
          <Users className="h-4 w-4 text-accent-ink" aria-hidden="true" />
          Latest leads
        </h2>
        <Link href="/portal/leads" className="inline-flex min-h-11 shrink-0 items-center gap-1 whitespace-nowrap text-sm font-medium text-accent-ink hover:underline">
          All leads <ArrowUpRight className="h-4 w-4" aria-hidden="true" />
        </Link>
      </div>
      {leads === null && loaded ? (
        <p className="p-5 text-sm text-fg-muted sm:p-6">Couldn&apos;t load your leads.</p>
      ) : leads === null ? (
        <div className="space-y-3 p-5 sm:p-6">
          <Skeleton className="h-11" />
          <Skeleton className="h-11" />
          <Skeleton className="h-11" />
        </div>
      ) : leads.length === 0 ? (
        <p className="p-5 text-sm text-fg-muted sm:p-6">No leads yet. They appear here the moment Hermes captures one.</p>
      ) : (
        <ul className="divide-y divide-line">
          {leads.map((l) => {
            const st = leadStatus(l.status);
            return (
              <li key={l.id} className="flex items-center gap-4 px-5 py-3.5 sm:px-6">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[15px] font-medium text-fg">{l.name || l.phone || "Unnamed lead"}</p>
                  <p className="mt-0.5 truncate text-xs text-fg-subtle">
                    {[l.service, sourceLabel(l.source), relativeTime(l.created_at)].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <Badge tone={st.tone}>{st.label}</Badge>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-8">
      <Skeleton className="h-52 rounded-[var(--p-radius-xl)]" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-40 rounded-[var(--card-radius)]" />
        ))}
      </div>
    </div>
  );
}
