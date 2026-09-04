import { useId } from "react";

/**
 * The run's publication: free text with suggestions (NCERT, Balbharati, …).
 * Contract v2.0 §18 / register Q27: this one value is printed as
 * ``concept_source`` and ``question_source`` on every row of all four
 * outputs. Concepts arriving from a second book still merge into existing
 * entries with their provenance accumulated in the database.
 */
export default function SourceBookInput({
  value,
  onChange,
  options,
  disabled = false,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  disabled?: boolean;
}) {
  const inputId = useId();

  return (
    <div className="field">
      <label className="field-label" htmlFor={inputId}>
        Source (publication)
      </label>
      <input
        className="input-md"
        id={inputId}
        list="book-sources-list"
        value={value}
        disabled={disabled}
        placeholder="e.g. NCERT, Balbharati…"
        onChange={(e) => onChange(e.target.value)}
      />
      <datalist id="book-sources-list">
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
      <div className="hint mt-4">
        Printed as the concept source and question source on every row of
        the four outputs. Leave it blank and the database upload is blocked
        until one is supplied.
      </div>
    </div>
  );
}
