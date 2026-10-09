"use client";

import { Check, ChevronDown, Plug } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { SessionGate } from "@/components/SessionGate";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field as FormField, Input, Select, Textarea } from "@/components/ui/Input";
import { Notice } from "@/components/ui/Notice";
import { PageHeader } from "@/components/ui/PageHeader";
import { cn } from "@/lib/cn";
import { createResource, listResource, Row } from "@/lib/business";

/**
 * Guided onboarding, inside the portal and driven by the session.
 *
 * Each step writes to the same /business/{resource} API the agents already
 * read from, so finishing a step changes the product rather than a form's
 * local state. Steps whose backing integration is not proven end to end are
 * shown as not connected and link to Connections instead of pretending.
 */

interface Field {
  name: string;
  label: string;
  placeholder?: string;
  required?: boolean;
  hint?: string;
  /** Input control; values are still submitted as before (see NUMERIC). */
  kind?: "text" | "number" | "time" | "day" | "textarea";
}

interface Step {
  key: string;
  title: string;
  blurb: string;
  resource: string;
  fields: Field[];
  summary: (row: Row) => string;
}

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const STEPS: Step[] = [
  {
    key: "location",
    title: "Where you operate",
    blurb: "Your address and timezone. The AI agent quotes travel, hours and directions from this.",
    resource: "locations",
    fields: [
      { name: "name", label: "Branch name", placeholder: "Main branch", required: true },
      { name: "address", label: "Address", placeholder: "Street address" },
      { name: "city", label: "City", placeholder: "Lahore" },
      { name: "country", label: "Country", placeholder: "Pakistan" },
      { name: "phone", label: "Phone", placeholder: "+92 300 0000000" },
      { name: "timezone", label: "Timezone", placeholder: "Asia/Karachi" },
    ],
    summary: (r) => [r.name, r.city].filter(Boolean).join(" — "),
  },
  {
    key: "services",
    title: "What you sell",
    blurb: "Every service with its price. The agent will not quote a price that is not on this list.",
    resource: "services",
    fields: [
      { name: "name", label: "Service", placeholder: "Hair PRP", required: true },
      { name: "category", label: "Category", placeholder: "Treatments" },
      { name: "price", label: "Price", placeholder: "15000", kind: "number", hint: "Numbers only" },
      { name: "currency", label: "Currency", placeholder: "PKR" },
      { name: "duration_minutes", label: "Duration (minutes)", placeholder: "60", kind: "number" },
    ],
    summary: (r) => [r.name, r.price != null ? `${r.price}${r.currency ? ` ${r.currency}` : ""}` : null].filter(Boolean).join(" — "),
  },
  {
    key: "hours",
    title: "When you are open",
    blurb: "Opening hours per day. Bookings outside these hours are refused rather than promised.",
    resource: "business_hours",
    fields: [
      { name: "day_of_week", label: "Day", required: true, kind: "day" },
      { name: "opens", label: "Opens", placeholder: "11:00", kind: "time" },
      { name: "closes", label: "Closes", placeholder: "20:00", kind: "time" },
    ],
    summary: (r) => `${DAYS[Number(r.day_of_week)] ?? `Day ${r.day_of_week}`}: ${r.opens ?? "?"}–${r.closes ?? "?"}`,
  },
  {
    key: "policies",
    title: "Your rules",
    blurb: "Deposits, cancellations, refunds. The agent answers from these instead of improvising.",
    resource: "business_policies",
    fields: [
      { name: "kind", label: "Kind", placeholder: "cancellation", required: true },
      { name: "title", label: "Title", placeholder: "24-hour cancellation", required: true },
      { name: "body", label: "Policy", placeholder: "Describe the policy", required: true, kind: "textarea" },
    ],
    summary: (r) => String(r.title ?? r.kind ?? ""),
  },
];

const NUMERIC = new Set(["price", "duration_minutes", "day_of_week"]);

export default function OnboardingPage() {
  return (
    <SessionGate title="Set up business" purpose="set up your business">
      {() => <Onboarding />}
    </SessionGate>
  );
}

function Onboarding() {
  const [rows, setRows] = useState<Record<string, Row[]>>({});
  const [open, setOpen] = useState<string>(STEPS[0].key);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [savedStep, setSavedStep] = useState<string | null>(null);

  const refresh = useCallback(
    () =>
      Promise.all(
        STEPS.map(async (s) => [s.key, (await listResource(s.resource)) ?? []] as const),
      ).then((entries) => setRows(Object.fromEntries(entries))),
    [],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const done = STEPS.filter((s) => (rows[s.key]?.length ?? 0) > 0).length;

  async function save(step: Step) {
    setBusy(true);
    setError(null);
    setSavedStep(null);
    const body: Row = {};
    for (const f of step.fields) {
      const raw = (draft[f.name] ?? "").trim();
      if (!raw) continue;
      body[f.name] = NUMERIC.has(f.name) ? Number(raw) : raw;
    }
    const missing = step.fields.filter((f) => f.required && body[f.name] === undefined);
    if (missing.length) {
      setFieldErrors(Object.fromEntries(missing.map((f) => [f.name, `${f.label} is required`])));
      setError(`${missing.map((f) => f.label).join(", ")} required`);
      setBusy(false);
      return;
    }
    setFieldErrors({});
    const created = await createResource(step.resource, body);
    if (!created) setError("Could not save. Your session may have expired — sign in again and retry.");
    else {
      setDraft({});
      setSavedStep(step.key);
      await refresh();
    }
    setBusy(false);
  }

  return (
    <div className="max-w-3xl">
      <PageHeader
        title="Set up your business"
        description="Your AI agent answers customers from what you enter here, so anything left blank is something it will not know."
      />

      <div className="mt-6">
        <div className="flex items-center justify-between text-sm">
          <span id="progress-label" className="font-medium text-fg">
            {done} of {STEPS.length} steps complete
          </span>
          {done === STEPS.length ? <Badge tone="success">All set</Badge> : null}
        </div>
        <div
          role="progressbar"
          aria-labelledby="progress-label"
          aria-valuemin={0}
          aria-valuemax={STEPS.length}
          aria-valuenow={done}
          className="mt-2 h-2 w-full overflow-hidden rounded-full bg-surface-3"
        >
          <div
            className="h-full rounded-full bg-[linear-gradient(90deg,var(--p-violet-600),var(--p-violet-400))] transition-[width] duration-500"
            style={{ width: `${(done / STEPS.length) * 100}%` }}
          />
        </div>
      </div>

      <ol className="mt-8 flex flex-col gap-3">
        {STEPS.map((step, idx) => {
          const saved = rows[step.key] ?? [];
          const isOpen = open === step.key;
          const complete = saved.length > 0;
          const panelId = `step-${step.key}`;
          return (
            <li key={step.key}>
              <Card as="section" className={cn("overflow-hidden", isOpen && "border-accent-line")}>
                <h2>
                  <button
                    type="button"
                    aria-expanded={isOpen}
                    aria-controls={panelId}
                    onClick={() => {
                      setOpen(isOpen ? "" : step.key);
                      setError(null);
                      setFieldErrors({});
                    }}
                    className="flex min-h-16 w-full items-center gap-4 px-5 py-4 text-left sm:px-6"
                  >
                    <span
                      className={cn(
                        "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-sm font-medium",
                        complete ? "border-success/40 bg-success-tint text-success" : "border-line-strong text-fg-muted",
                      )}
                    >
                      {complete ? <Check className="h-4 w-4" aria-label="Done" /> : idx + 1}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-base font-semibold text-fg">{step.title}</span>
                      <span className="block text-sm text-fg-subtle">
                        {complete ? `${saved.length} saved` : "Needs your details"}
                      </span>
                    </span>
                    <ChevronDown
                      className={cn("h-5 w-5 shrink-0 text-fg-subtle transition-transform", isOpen && "rotate-180")}
                      aria-hidden="true"
                    />
                  </button>
                </h2>

                {isOpen ? (
                  <div id={panelId} className="border-t border-line px-5 py-5 sm:px-6">
                    <p className="text-[15px] text-fg-muted">{step.blurb}</p>

                    {saved.length > 0 ? (
                      <ul className="mt-4 flex flex-wrap gap-2">
                        {saved.map((r, i) => (
                          <li key={String(r.id ?? i)}>
                            <Badge tone="neutral">{step.summary(r)}</Badge>
                          </li>
                        ))}
                      </ul>
                    ) : null}

                    <form
                      className="mt-6"
                      noValidate
                      onSubmit={(e) => {
                        e.preventDefault();
                        void save(step);
                      }}
                    >
                      <div className="grid gap-4 sm:grid-cols-2">
                        {step.fields.map((f) => (
                          <FormField
                            key={f.name}
                            label={f.label}
                            hint={f.hint}
                            required={f.required}
                            error={fieldErrors[f.name]}
                            className={f.kind === "textarea" ? "sm:col-span-2" : undefined}
                          >
                            {(p) => <Control field={f} value={draft[f.name] ?? ""} onChange={(v) => setDraft({ ...draft, [f.name]: v })} {...p} />}
                          </FormField>
                        ))}
                      </div>

                      <div className="mt-6 flex flex-col gap-3" aria-live="polite">
                        {error ? <Notice tone="danger" title="Not saved">{error}</Notice> : null}
                        {savedStep === step.key && !error ? <Notice tone="success" title="Saved">Your AI team can use this now.</Notice> : null}
                      </div>

                      <Button type="submit" disabled={busy} className="mt-4">
                        {busy ? "Saving…" : `Add to ${step.title.toLowerCase()}`}
                      </Button>
                    </form>
                  </div>
                ) : null}
              </Card>
            </li>
          );
        })}
      </ol>

      <Card className="mt-3 flex flex-col gap-3 border-dashed p-5 sm:flex-row sm:items-start sm:p-6">
        <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line-strong text-fg-muted">
          <Plug className="h-4 w-4" aria-hidden="true" />
        </span>
        <div>
          <h2 className="text-base font-semibold text-fg">WhatsApp and Meta</h2>
          <p className="mt-1 text-[15px] text-fg-muted">
            Not connected from here yet. We set these up with you for now; you can see their status on{" "}
            <Link className="font-medium text-accent-ink underline-offset-2 hover:underline" href="/portal/integrations">
              Connections
            </Link>
            .
          </p>
        </div>
      </Card>
    </div>
  );
}

function Control({
  field,
  value,
  onChange,
  ...a11y
}: {
  field: Field;
  value: string;
  onChange: (v: string) => void;
  id: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  required?: boolean;
}) {
  if (field.kind === "day") {
    return (
      <Select {...a11y} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose a day</option>
        {DAYS.map((d, i) => (
          <option key={d} value={String(i)}>
            {d}
          </option>
        ))}
      </Select>
    );
  }
  if (field.kind === "textarea") {
    return <Textarea {...a11y} value={value} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} />;
  }
  return (
    <Input
      {...a11y}
      type={field.kind === "time" ? "time" : "text"}
      inputMode={field.kind === "number" ? "decimal" : undefined}
      value={value}
      placeholder={field.placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
