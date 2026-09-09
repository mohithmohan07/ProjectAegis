/**
 * Human-facing labels for hierarchy titles.
 *
 * Workbook title cells carry a trailing identity tag for round-tripping. The
 * tag is useful to the importer but is not a label for a person to read. Keep
 * this matcher deliberately narrow so ordinary parenthetical text such as
 * "(Part1)" or "(poem)" remains visible.
 */
const TRAILING_INTERNAL_TAG =
  /\s*\((?:(?:\d{2}_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)|(?:\d{2}[A-Z]{4,}_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*))\)\s*$/i;

const INTERNAL_MACHINE_ID =
  /^(?:(?:\d{2}_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)|(?:\d{2}[A-Z]{4,}_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*))$/i;

function text(value: unknown): string {
  return String(value ?? "").trim().replace(/\s+/g, " ");
}

/** Remove only the recognized trailing workbook identity tag. */
export function stripInternalTitleTag(value: unknown): string {
  return text(value).replace(TRAILING_INTERNAL_TAG, "").trim();
}

/**
 * Choose a human label, preferring the persisted clean display name.
 * Machine IDs are never used as a fallback when both human fields are absent.
 */
export function displayLabel(
  displayName: unknown,
  title: unknown,
  fallback: string,
): string {
  for (const candidate of [displayName, title]) {
    const raw = text(candidate);
    if (!raw || INTERNAL_MACHINE_ID.test(raw)) continue;
    const clean = stripInternalTitleTag(raw);
    if (clean && !INTERNAL_MACHINE_ID.test(clean)) return clean;
  }
  return fallback;
}
