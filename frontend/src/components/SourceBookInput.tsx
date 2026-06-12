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
  return (
    <div className="field">
      <div className="field-label">Source book (for multi-source tagging)</div>
      <input
        list="book-sources-list"
        value={value}
        disabled={disabled}
        placeholder="e.g. NCERT, RD Sharma…"
        onChange={(e) => onChange(e.target.value)}
        style={{ maxWidth: 320 }}
      />
      <datalist id="book-sources-list">
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </div>
  );
}
