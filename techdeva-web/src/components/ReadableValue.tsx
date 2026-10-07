import { humanize } from "@/lib/labels";

/**
 * Renders whatever an agent stored (a string, a list, a nested object) as
 * readable text instead of raw JSON. Shows the same data, nothing added.
 *
 * - "Key: value" lines in a string become a definition list.
 * - Arrays become bullet lists; objects become labelled rows.
 */
export function ReadableValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined || value === "") return null;

  if (typeof value === "string") return <ReadableText text={value} />;
  if (typeof value === "number" || typeof value === "boolean") {
    return <span className="tabular-nums">{String(value)}</span>;
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-fg-subtle">None</span>;
    return (
      <ul className="list-disc space-y-1 pl-5 marker:text-fg-subtle">
        {value.map((v, i) => (
          <li key={i}>
            <ReadableValue value={v} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  }

  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).filter(
      ([, v]) => v !== null && v !== undefined && v !== "",
    );
    if (entries.length === 0) return null;
    if (depth > 2) return <code className="text-xs text-fg-muted">{JSON.stringify(value)}</code>;
    return (
      <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-[minmax(7rem,max-content)_1fr]">
        {entries.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-sm text-fg-subtle">{humanize(k)}</dt>
            <dd className="min-w-0 text-[15px] text-fg">
              <ReadableValue value={v} depth={depth + 1} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }

  return <span>{String(value)}</span>;
}

function ReadableText({ text }: { text: string }) {
  const lines = text.split(/\n+/).map((l) => l.trim()).filter(Boolean);
  const pairs = lines.map((l) => /^([A-Za-z][\w ()/&-]{0,40}):\s+(.+)$/.exec(l));
  if (lines.length > 1 && pairs.every(Boolean)) {
    return (
      <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-[minmax(7rem,max-content)_1fr]">
        {pairs.map((m, i) => (
          <div key={i} className="contents">
            <dt className="text-sm text-fg-subtle">{m![1]}</dt>
            <dd className="min-w-0 text-[15px] text-fg">{m![2]}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return (
    <div className="space-y-2 text-[15px] leading-relaxed text-fg">
      {lines.map((l, i) => (
        <p key={i}>{l}</p>
      ))}
    </div>
  );
}
