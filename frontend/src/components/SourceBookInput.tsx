import { useId } from "react";

/**
 * The run's publication: free text with suggestions (NCERT, Balbharati, …).
 * It becomes the Concept Source and the extracted Post-Learning Question
 * Source. Generated Pre-Learning questions use UpSchool DB as their Question
 * Source. Concepts arriving from a second book still merge into existing
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
        Your selected publication is used as the Concept Source and the
        extracted Post-Learning Question Source. Generated Pre-Learning
        questions use UpSchool DB as their Question Source. Leave it blank and
        the database upload is blocked until one is supplied.
      </div>
    </div>
  );
}
