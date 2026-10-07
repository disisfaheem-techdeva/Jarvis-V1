"use client";

import { Check, CircleCheck, Hand, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ReadableValue } from "@/components/ReadableValue";
import { SessionGate } from "@/components/SessionGate";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Field, Textarea } from "@/components/ui/Input";
import { Notice } from "@/components/ui/Notice";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { TabPanel, Tabs } from "@/components/ui/Tabs";
import { API_BASE } from "@/lib/api";
import { agentLabel, decisionStatus, kindLabel, relativeTime } from "@/lib/labels";

/**
 * The approval queue.
 *
 * The orchestrator stops before anything irreversible and waits for a human.
 * Until now that decision had no screen, so work parked at the gate stayed
 * parked. Approving here resumes the task server-side; rejecting closes it
 * with a recorded reason. Nothing is published or spent without a click here.
 *
 * Decisions are applied optimistically (the card leaves the queue at once) and
 * restored with an error if the server refuses, so the action feels instant
 * without ever claiming something happened that didn't.
 */

interface Deliverable {
  id: string;
  task_id: string;
  agent: string;
  kind: string;
  title: string;
  body: Record<string, unknown> | null;
  status: string;
  requires_approval: boolean;
  decision_reason?: string | null;
  created_at?: string;
}

interface Payload {
  deliverables: Deliverable[];
  awaiting_approval: number;
}

type Result = { kind: "approve" | "reject"; title: string } | null;

export default function ApprovalsPage() {
  return (
    <SessionGate title="Approvals" purpose="review what is waiting" skeleton={<QueueSkeleton />}>
      {() => <Approvals />}
    </SessionGate>
  );
}

function Approvals() {
  const [data, setData] = useState<Payload | null | undefined>(undefined);
  const [busy, setBusy] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState("waiting");

  const load = useCallback(() => {
    fetch(`${API_BASE}/portal/deliverables`, { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => setData(null));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function decide(d: Deliverable, action: "approve" | "reject", why?: string) {
    setBusy(d.id);
    setError(null);
    setResult(null);
    setHidden((h) => new Set(h).add(d.id));
    try {
      const res = await fetch(`${API_BASE}/portal/deliverables/${d.id}/${action}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: action === "reject" ? JSON.stringify({ reason: why }) : undefined,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        setError(detail?.detail ?? `Could not ${action} this item.`);
        restore(d.id);
      } else {
        setRejecting(null);
        setReason("");
        setResult({ kind: action, title: d.title });
        load();
      }
    } catch {
      setError("Network error — nothing was changed.");
      restore(d.id);
    }
    setBusy(null);
  }

  function restore(id: string) {
    setHidden((h) => {
      const next = new Set(h);
      next.delete(id);
      return next;
    });
  }

  if (data === undefined) return <QueueSkeleton />;

  const header = (
    <PageHeader
      title="Approvals"
      description="Your AI team stops here before anything goes out or costs money. Nothing below has happened yet."
    />
  );

  if (data === null) {
    return (
      <div className="max-w-3xl">
        {header}
        <Notice
          tone="offline"
          title="Couldn't load your approvals"
          className="mt-6"
          action={
            <Button variant="secondary" size="sm" onClick={load}>
              Try again
            </Button>
          }
        >
          Nothing was approved or rejected.
        </Notice>
      </div>
    );
  }

  const pending = data.deliverables.filter((d) => d.status === "awaiting_approval" && !hidden.has(d.id));
  const decided = data.deliverables.filter((d) => d.status !== "awaiting_approval");

  return (
    <div className="max-w-3xl">
      {header}

      <div className="mt-6 flex flex-col gap-3" aria-live="polite">
        {result ? (
          <Notice tone="success" title={result.kind === "approve" ? "Approved" : "Rejected"}>
            {result.kind === "approve"
              ? `“${result.title}” is approved. Your AI team carries it out from here.`
              : `“${result.title}” was rejected and your reason was recorded. Your AI team learns from it.`}
          </Notice>
        ) : null}
        {error ? (
          <Notice tone="danger" title="That didn't go through">
            {error} The item is back in your queue.
          </Notice>
        ) : null}
      </div>

      <Tabs
        id="approvals"
        className="mt-6"
        label="Approvals"
        value={tab}
        onChange={setTab}
        items={[
          { value: "waiting", label: "Waiting for you", count: pending.length },
          { value: "decided", label: "Already decided", count: decided.length },
        ]}
      />

      {tab === "waiting" ? (
        <TabPanel tabsId="approvals" value="waiting" className="mt-6">
          {pending.length === 0 ? (
            <EmptyState
              icon={CircleCheck}
              title="Nothing is waiting on you"
              body="When your AI team wants to spend money, publish something or change a price, it will appear here first."
            />
          ) : (
            <ul className="flex flex-col gap-4">
              {pending.map((d) => (
                <li key={d.id}>
                  <Card as="article" aria-labelledby={`t-${d.id}`} className="overflow-hidden">
                    <header className="px-5 pt-5 sm:px-6 sm:pt-6">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="flex items-center gap-2 text-sm text-fg-muted">
                          <Hand className="h-4 w-4 text-warning" aria-hidden="true" />
                          {agentLabel(d.agent)} asks · {relativeTime(d.created_at)}
                        </p>
                        <Badge tone="accent">{kindLabel(d.kind)}</Badge>
                      </div>
                      <h2 id={`t-${d.id}`} className="mt-2 text-lg font-semibold leading-snug text-fg">
                        {d.title}
                      </h2>
                    </header>

                    <div className="px-5 py-5 sm:px-6">
                      {d.body && JSON.stringify(d.body.work ?? d.body) !== "{}" ? (
                        <div className="rounded-[var(--p-radius-md)] border border-line bg-bg-elevated p-4">
                          <ReadableValue value={d.body.work ?? d.body} />
                        </div>
                      ) : (
                        <p className="text-sm text-fg-subtle">The agent recorded no detail for this item.</p>
                      )}
                    </div>

                    {rejecting === d.id ? (
                      <form
                        className="border-t border-line bg-surface-2 px-5 py-5 sm:px-6"
                        onSubmit={(e) => {
                          e.preventDefault();
                          if (reason.trim()) void decide(d, "reject", reason.trim());
                        }}
                      >
                        <Field label="Why are you rejecting this?" hint="Your AI team learns from the reason." required>
                          {(p) => (
                            <Textarea {...p} autoFocus value={reason} onChange={(e) => setReason(e.target.value)} rows={2} />
                          )}
                        </Field>
                        <div className="mt-4 flex flex-wrap gap-2">
                          <Button type="submit" variant="danger" disabled={busy === d.id || !reason.trim()}>
                            <X className="h-4 w-4" aria-hidden="true" />
                            Confirm reject
                          </Button>
                          <Button
                            variant="ghost"
                            onClick={() => {
                              setRejecting(null);
                              setReason("");
                            }}
                          >
                            Cancel
                          </Button>
                        </div>
                      </form>
                    ) : (
                      <div className="grid grid-cols-2 gap-2 border-t border-line px-5 py-4 sm:flex sm:px-6">
                        <Button variant="approve" disabled={busy === d.id} onClick={() => void decide(d, "approve")}>
                          <Check className="h-4 w-4" aria-hidden="true" />
                          Approve
                        </Button>
                        <Button variant="danger" disabled={busy === d.id} onClick={() => setRejecting(d.id)}>
                          <X className="h-4 w-4" aria-hidden="true" />
                          Reject
                        </Button>
                      </div>
                    )}
                  </Card>
                </li>
              ))}
            </ul>
          )}
        </TabPanel>
      ) : (
        <TabPanel tabsId="approvals" value="decided" className="mt-6">
          {decided.length === 0 ? (
            <p className="text-sm text-fg-muted">You haven&apos;t decided anything yet.</p>
          ) : (
            <Card>
              <ul className="divide-y divide-line">
                {decided.slice(0, 20).map((d) => {
                  const st = decisionStatus(d.status);
                  return (
                    <li key={d.id} className="flex flex-wrap items-start justify-between gap-3 px-5 py-4 sm:px-6">
                      <div className="min-w-0 flex-1">
                        <p className="text-[15px] font-medium text-fg">{d.title}</p>
                        <p className="mt-0.5 text-xs text-fg-subtle">
                          {agentLabel(d.agent)} · {relativeTime(d.created_at)}
                        </p>
                        {d.decision_reason ? (
                          <p className="mt-2 text-sm text-fg-muted">Reason: {d.decision_reason}</p>
                        ) : null}
                      </div>
                      <Badge tone={st.tone}>{st.label}</Badge>
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}
        </TabPanel>
      )}
    </div>
  );
}

function QueueSkeleton() {
  return (
    <div className="max-w-3xl space-y-6">
      <Skeleton className="h-9 w-48" />
      <Skeleton className="h-5 w-96 max-w-full" />
      <Skeleton className="h-64 rounded-[var(--card-radius)]" />
      <Skeleton className="h-64 rounded-[var(--card-radius)]" />
    </div>
  );
}
