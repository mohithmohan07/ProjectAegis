/** Keep legacy usage receipts distinct from complete provider-attempt counts. */
interface RequestUsage {
  request_count?: number;
  provider_request_count?: number;
  attempt_coverage_complete?: boolean;
  usage_complete?: boolean;
  missing_usage_response_count?: number;
  pending_request_count?: number;
  unresolved_usage_request_count?: number;
}

interface CostUsage extends RequestUsage {
  estimated_cost_usd?: number | null;
  known_usage_estimated_cost_usd?: number;
  pricing_complete?: boolean;
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
    || finiteUsageNumber(usage.unresolved_usage_request_count) > 0
    // Older receipts cannot distinguish running requests from lost usage.
    // Keep their conservative status without mislabelling new pending calls.
    || finiteUsageNumber(usage.provider_request_count)
      > finiteUsageNumber(usage.request_count) + finiteUsageNumber(usage.pending_request_count);
}

/** A known subtotal is useful, but it must never masquerade as a full bill. */
export function usageCost(usage: CostUsage) {
  const usageGap = hasUsageGap(usage);
  const pendingCount = finiteUsageNumber(usage.pending_request_count);
  // Legacy summaries used pricing_complete=false for missing receipts too.
  // New counter-bearing rows separate rate coverage from missing usage, so
  // both defects can be reported honestly even when both counters are zero.
  const separateRequestStates = usage.pending_request_count !== undefined
    || usage.unresolved_usage_request_count !== undefined;
  const pricingMissing = usage.pricing_complete === false && (!usageGap || separateRequestStates);
  const total = usage.estimated_cost_usd;
  const complete = !usageGap && !pricingMissing
    && typeof total === "number" && Number.isFinite(total);
  const known = usage.known_usage_estimated_cost_usd;
  const value = complete ? total
    : typeof known === "number" && Number.isFinite(known) ? known : null;
  return {
    value,
    complete,
    recordedOnly: !complete || pendingCount > 0,
    usageGap,
    pendingCount,
    pricingMissing,
  };
}

export function usageCostNotes(usage: CostUsage): string[] {
  const cost = usageCost(usage);
  const notes: string[] = [];
  if (cost.pendingCount > 0) {
    notes.push(`${cost.pendingCount} provider request${cost.pendingCount === 1 ? " is" : "s are"} still running; their tokens and cost will appear when reported.`);
  }
  if (cost.usageGap) {
    notes.push("Token usage is missing for one or more provider requests. Recorded estimates exclude unresolved charges; the full total is unknown.");
  }
  if (cost.pricingMissing || (!cost.complete && !cost.usageGap)) {
    notes.push("Pricing is not configured for every model or service tier used. Recorded estimates exclude unpriced usage; the full total is unknown.");
  }
  return notes;
}
