import { useId } from "react";

/**
 * Book-source picker: free text with suggestions (NCERT, RD Sharma, …).
 * Concepts/questions arriving from a second book merge into existing entries
 * with their sources accumulated, instead of duplicating.
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
        Source book (for multi-source tagging)
      </label>
      <input
        className="input-md"
        id={inputId}
        list="book-sources-list"
        value={value}
        disabled={disabled}
        placeholder="e.g. NCERT, RD Sharma…"
        onChange={(e) => onChange(e.target.value)}
      />
      <datalist id="book-sources-list">
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </div>
  );
}
