"use client";

import { MessageCircle, Phone, Search, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { SessionGate } from "@/components/SessionGate";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Notice } from "@/components/ui/Notice";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/Table";
import { getLeads, type Lead } from "@/lib/api";
import { cn } from "@/lib/cn";
import { leadStatus, relativeTime, sourceLabel } from "@/lib/labels";

export default function LeadsPage() {
  return (
    <SessionGate title="Leads" purpose="see the people your AI team is talking to" skeleton={<LeadsSkeleton />}>
      {(session) => <Leads tenantId={session.tenant_id} />}
    </SessionGate>
  );
}

function digits(phone?: string) {
  return (phone ?? "").replace(/[^\d]/g, "");
}

function Leads({ tenantId }: { tenantId: string | null }) {
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<string>("all");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!tenantId) return;
    getLeads(tenantId).then((r) => (r.ok ? setLeads(r.data) : setError(r.error)));
  }, [tenantId, attempt]);

  const statuses = useMemo(() => {
    const counts = new Map<string, number>();
    for (const l of leads ?? []) counts.set(l.status, (counts.get(l.status) ?? 0) + 1);
    return [...counts.entries()];
  }, [leads]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return [...(leads ?? [])]
      .filter((l) => status === "all" || l.status === status)
      .filter((l) => !q || [l.name, l.phone, l.service].some((v) => v?.toLowerCase().includes(q)))
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  }, [leads, query, status]);

  const header = (
    <PageHeader
      title="Leads"
      description="Everyone who has contacted your business, newest first. Tap a number to call or message them."
    />
  );

  if (!tenantId) {
    return (
      <div>
        {header}
        <EmptyState className="mt-6" icon={Users} title="Your login isn't linked to a business yet" body="Leads appear here once your account is linked." />
      </div>
    );
  }

  if (error) {
    return (
      <div>
        {header}
        <Notice
          tone="offline"
          title="Couldn't load your leads"
          className="mt-6"
          action={
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setError(null);
                setAttempt((n) => n + 1);
              }}
            >
              Try again
            </Button>
          }
        >
          {error}. Nothing was changed.
        </Notice>
      </div>
    );
  }

  if (!leads) return <LeadsSkeleton />;

  return (
    <div>
      {header}

      {leads.length === 0 ? (
        <EmptyState
          className="mt-8"
          icon={Users}
          title="No leads yet"
          body="When someone messages you on WhatsApp or replies to an ad, Hermes records them here."
        />
      ) : (
        <>
          <div className="mt-8 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-2">
              <FilterChip active={status === "all"} onClick={() => setStatus("all")} label="All" count={leads.length} />
              {statuses.map(([s, n]) => (
                <FilterChip key={s} active={status === s} onClick={() => setStatus(s)} label={leadStatus(s).label} count={n} />
              ))}
            </div>
            <div className="relative w-full lg:w-72">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle" aria-hidden="true" />
              <Input
                type="search"
                aria-label="Search leads by name, phone or service"
                placeholder="Search name, phone, service"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="pl-9"
              />
            </div>
          </div>

          <p className="mt-4 text-sm text-fg-subtle" aria-live="polite">
            Showing {visible.length} of {leads.length}
          </p>

          {visible.length === 0 ? (
            <p className="mt-6 text-sm text-fg-muted">No leads match. Clear the search or pick another status.</p>
          ) : (
            <>
              {/* Desktop table */}
              <div className="mt-3 hidden md:block">
                <Table caption="Leads">
                  <THead>
                    <tr>
                      <TH>Name</TH>
                      <TH>Phone</TH>
                      <TH>Service</TH>
                      <TH>Source</TH>
                      <TH>Status</TH>
                      <TH className="text-right">Came in</TH>
                    </tr>
                  </THead>
                  <TBody>
                    {visible.map((l) => {
                      const st = leadStatus(l.status);
                      return (
                        <TR key={l.id}>
                          <TD className="font-medium">{l.name || <span className="text-fg-subtle">No name yet</span>}</TD>
                          <TD className="tabular-nums">
                            {l.phone ? (
                              <a href={`tel:${l.phone}`} className="hover:text-accent-ink hover:underline">
                                {l.phone}
                              </a>
                            ) : (
                              "—"
                            )}
                          </TD>
                          <TD className="text-fg-muted">{l.service ?? "—"}</TD>
                          <TD className="text-fg-muted">{sourceLabel(l.source)}</TD>
                          <TD>
                            <Badge tone={st.tone}>{st.label}</Badge>
                          </TD>
                          <TD className="text-right text-fg-muted">
                            <time dateTime={l.created_at} title={new Date(l.created_at).toLocaleString()}>
                              {relativeTime(l.created_at)}
                            </time>
                          </TD>
                        </TR>
                      );
                    })}
                  </TBody>
                </Table>
              </div>

              {/* Phone list */}
              <ul className="mt-3 flex flex-col gap-3 md:hidden">
                {visible.map((l) => {
                  const st = leadStatus(l.status);
                  const d = digits(l.phone);
                  return (
                    <li key={l.id}>
                      <Card className="p-4">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate text-base font-medium text-fg">{l.name || l.phone || "No name yet"}</p>
                            <p className="mt-0.5 text-sm text-fg-muted">
                              {[l.service, sourceLabel(l.source)].filter(Boolean).join(" · ")}
                            </p>
                            <p className="mt-0.5 text-xs text-fg-subtle">{relativeTime(l.created_at)}</p>
                          </div>
                          <Badge tone={st.tone}>{st.label}</Badge>
                        </div>
                        {d ? (
                          <div className="mt-4 grid grid-cols-2 gap-2">
                            <a
                              href={`tel:${l.phone}`}
                              className="inline-flex h-11 items-center justify-center gap-2 rounded-[var(--btn-radius)] border border-line-strong text-sm font-medium text-fg"
                            >
                              <Phone className="h-4 w-4" aria-hidden="true" /> Call
                            </a>
                            <a
                              href={`https://wa.me/${d}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex h-11 items-center justify-center gap-2 rounded-[var(--btn-radius)] border border-line-strong text-sm font-medium text-fg"
                            >
                              <MessageCircle className="h-4 w-4" aria-hidden="true" /> WhatsApp
                            </a>
                          </div>
                        ) : null}
                      </Card>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </>
      )}
    </div>
  );
}

function FilterChip({ active, onClick, label, count }: { active: boolean; onClick: () => void; label: string; count: number }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex h-11 items-center gap-2 rounded-full border px-3.5 sm:h-9 text-sm font-medium transition-colors",
        active ? "border-accent-line bg-accent-tint text-fg" : "border-line-strong text-fg-muted hover:text-fg",
      )}
    >
      {label}
      <span className="tabular-nums text-xs text-fg-subtle">{count}</span>
    </button>
  );
}

function LeadsSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-9 w-40" />
      <Skeleton className="h-5 w-96 max-w-full" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-72 rounded-[var(--card-radius)]" />
    </div>
  );
}
