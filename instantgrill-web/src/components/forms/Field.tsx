import { WarningCircle } from "@phosphor-icons/react/dist/ssr";

export const fieldClass =
  "h-12 w-full rounded-[var(--radius-field)] border bg-coal-850 px-4 text-ash-50 placeholder:text-ash-400 transition-colors focus:border-ember-500 focus:outline-none";

export const borderFor = (error?: string) => (error ? "border-danger" : "border-coal-600");

/** Label above, control, then error (or hint) below. */
export function Field({ id, label, error, hint, children }: { id: string; label: string; error?: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium text-ash-100">
        {label}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} className="flex items-center gap-1.5 text-sm text-danger">
          <WarningCircle size={16} /> {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-sm text-ash-400">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
