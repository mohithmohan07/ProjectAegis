/** Keep legacy usage receipts distinct from complete provider-attempt counts. */
interface RequestUsage {
  request_count?: number;
  provider_request_count?: number;
  attempt_coverage_complete?: boolean;
  usage_complete?: boolean;
  missing_usage_response_count?: number;
}

export function finiteUsageNumber(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function providerRequestCount(usage: RequestUsage): number {
  const reported = finiteUsageNumber(usage.request_count);
  const physical = usage.provider_request_count;
  if (typeof physical !== "number" || !Number.isFinite(physical)) return reported;
  // Older merged segments can contain responses without attempt receipts.
  // Their known requests must not disappear behind a newer partial counter.
  return usage.attempt_coverage_complete === false
    ? Math.max(physical, reported)
    : physical;
}

export function hasUsageGap(usage: RequestUsage): boolean {
  return usage.usage_complete === false
    || finiteUsageNumber(usage.missing_usage_response_count) > 0
    || finiteUsageNumber(usage.provider_request_count) > finiteUsageNumber(usage.request_count);
}
