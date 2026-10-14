"use client";

/** Accessible single-choice pill group (a radio group styled as pills). */
export function ChoicePills<T extends string>({
  name,
  label,
  options,
  value,
  onChange,
  error,
}: {
  name: string;
  label: string;
  options: readonly T[];
  value: T | undefined;
  onChange: (v: T) => void;
  error?: boolean;
}) {
  return (
    <fieldset>
      <legend className={`text-sm font-medium ${error ? "text-danger" : "text-ash-100"}`}>
        {label} {error && <span className="font-normal">(required)</span>}
      </legend>
      <div className="mt-2 flex flex-wrap gap-2">
        {options.map((o) => (
          <label
            key={o}
            className={`cursor-pointer rounded-full px-4 py-2 text-sm ring-1 transition has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ember-400 ${
              value === o
                ? "bg-ember-500 font-medium text-coal-950 ring-ember-500"
                : "text-ash-100 ring-coal-600 hover:ring-ash-400"
            }`}
          >
            <input
              type="radio"
              name={name}
              value={o}
              checked={value === o}
              onChange={() => onChange(o)}
              className="sr-only"
            />
            {o}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
