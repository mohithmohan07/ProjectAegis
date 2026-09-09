import { describe, expect, it } from "vitest";
import {
  fmtStageElapsed,
  groupStages,
  latestStageOccurrenceIndexes,
  stageCost,
} from "./runStages";
import type { RunLine } from "../RunConsole";
import type { StageUsageRow } from "../types";

const line = (
  level: string, message: string, ts: number, lane?: string,
): RunLine => ({ level, message, ts, ...(lane ? { lane } : {}) });

describe("groupStages", () => {
  it("partitions lines into stage groups at each step", () => {
    const groups = groupStages([
      line("info", "warming up", 10),
      line("step", "Stage One", 12),
      line("info", "one-a", 13),
      line("warning", "careful", 14),
      line("step", "Stage Two", 20),
      line("error", "boom", 25),
    ]);
    expect(groups.map((g) => g.title)).toEqual([
      "Starting", "Stage One", "Stage Two",
    ]);
    expect(groups[1].startTs).toBe(12);
    expect(groups[1].endTs).toBe(14);
    expect(groups[1].hasWarning).toBe(true);
    expect(groups[1].hasError).toBe(false);
    expect(groups[2].hasError).toBe(true);
  });

  it("collects each stage's parallel lanes in first-spoken order", () => {
    const groups = groupStages([
      line("step", "Concepts", 1),
      line("info", "[Inventory · early track] chunk 1", 2, "Inventory · early track"),
      line("info", "mainline", 3),
      line("info", "[Place] placed", 4, "Place"),
      line("info", "[Inventory · early track] chunk 2", 5, "Inventory · early track"),
    ]);
    expect(groups[0].lanes).toEqual(["Inventory · early track", "Place"]);
    expect(groups[0].lines).toHaveLength(4);
  });

  it("assigns a same-title attempt total only to the newest card", () => {
    const groups = groupStages([
      line("step", "Parsing", 1),
      line("info", "first attempt", 2),
      line("step", "Assemble", 3),
      line("step", "Parsing", 4),
      line("info", "resumed attempt", 5),
      line("step", "Assemble", 6),
    ]);

    expect([...latestStageOccurrenceIndexes(groups)].sort()).toEqual([2, 3]);
    expect(groups.map((group) => group.title)).toEqual([
      "Parsing", "Assemble", "Parsing", "Assemble",
    ]);
  });
});

describe("stageCost", () => {
  const row = (
    stage: string, lane: string, cost: number | null, tokens = 100,
    cached = 0, cacheWrite = 0,
  ): StageUsageRow => ({
    stage, lane, request_count: 2, input_tokens: 60, output_tokens: 40,
    cached_input_tokens: cached, cache_write_tokens: cacheWrite,
    reasoning_tokens: 10, total_tokens: tokens,
    estimated_cost_usd: cost, pricing_complete: cost != null,
    first_ts: 1, last_ts: 2,
  });

  it("sums a stage's rows across lanes", () => {
    const cost = stageCost(
      [row("A", "", 0.5, 100, 20, 10),
       row("A", "Place", 0.25, 100, 15, 5),
       row("B", "", 1)],
      "A",
    );
    expect(cost).not.toBeNull();
    expect(cost!.requestCount).toBe(4);
    expect(cost!.totalTokens).toBe(200);
    expect(cost!.cachedInputTokens).toBe(35);
    expect(cost!.cacheWriteTokens).toBe(15);
    expect(cost!.cost).toBeCloseTo(0.75);
    expect(cost!.lanes.map((r) => r.lane)).toEqual(["Place"]);
  });

  it("never shows a partial number as a total", () => {
    const cost = stageCost([row("A", "", 0.5), row("A", "X", null)], "A");
    expect(cost!.cost).toBeNull();
    expect(cost!.costComplete).toBe(false);
    expect(cost!.knownCost).toBeCloseTo(0.5);
    expect(cost!.pricingMissing).toBe(true);
  });

  it("sums only server-provided INR values across the stage's lanes", () => {
    const cost = stageCost([
      { ...row("A", "Pre", 0.5), estimated_cost_inr: 41,
        inr_conversion_complete: true },
      { ...row("A", "Post", 0.25), estimated_cost_inr: 22,
        inr_conversion_complete: true },
      { ...row("B", "", 1), estimated_cost_inr: 90,
        inr_conversion_complete: true },
    ], "A")!;

    expect(cost.costInr).toBe(63);
    expect(cost.knownCostInr).toBe(63);
    expect(cost.costInrComplete).toBe(true);
    expect(cost.conversionMissing).toBe(false);
    expect(cost.cost).toBeCloseTo(0.75);
  });

  it("does not present converted lanes as the full INR total when another lane has no conversion", () => {
    const cost = stageCost([
      { ...row("A", "Pre", 0.5), estimated_cost_inr: 41,
        inr_conversion_complete: true },
      row("A", "Post", 0.25),
    ], "A")!;

    expect(cost.costInr).toBeNull();
    expect(cost.knownCostInr).toBe(41);
    expect(cost.costInrComplete).toBe(false);
    expect(cost.conversionMissing).toBe(true);
    expect(cost.cost).toBeCloseTo(0.75);
    expect(cost.pricingMissing).toBe(false);
  });

  it("leaves historical INR costs unavailable without inventing a conversion", () => {
    const cost = stageCost([row("A", "", 0.5)], "A")!;

    expect(cost.costInr).toBeNull();
    expect(cost.knownCostInr).toBeNull();
    expect(cost.costInrComplete).toBe(false);
    expect(cost.conversionMissing).toBe(true);
    expect(cost.cost).toBe(0.5);
  });

  it("preserves a recorded INR subtotal alongside pending and unresolved usage", () => {
    const cost = stageCost([
      { ...row("A", "Pre", 0.5), estimated_cost_inr: 41,
        inr_conversion_complete: true, provider_request_count: 4,
        pending_request_count: 2, usage_complete: true },
      { ...row("A", "Post", null), estimated_cost_inr: null,
        known_usage_estimated_cost_inr: 22, inr_conversion_complete: true,
        provider_request_count: 3, pending_request_count: 0,
        unresolved_usage_request_count: 1, usage_complete: false,
        known_usage_estimated_cost_usd: 0.25, pricing_complete: true },
    ], "A")!;

    expect(cost.costInr).toBeNull();
    expect(cost.knownCostInr).toBe(63);
    expect(cost.costInrComplete).toBe(false);
    expect(cost.conversionMissing).toBe(false);
    expect(cost.pendingCount).toBe(2);
    expect(cost.usageGap).toBe(true);
    expect(cost.pricingMissing).toBe(false);
  });

  it("preserves an explicitly converted zero", () => {
    const cost = stageCost([
      { ...row("A", "", 0), estimated_cost_inr: 0,
        inr_conversion_complete: true },
    ], "A")!;

    expect(cost.costInr).toBe(0);
    expect(cost.knownCostInr).toBe(0);
    expect(cost.costInrComplete).toBe(true);
    expect(cost.conversionMissing).toBe(false);
  });

  it("keeps the subtotal across lanes with pending and unresolved requests", () => {
    const cost = stageCost([
      { ...row("A", "Pre", 0.5), provider_request_count: 4,
        pending_request_count: 2, usage_complete: true },
      { ...row("A", "Post", null), provider_request_count: 3,
        pending_request_count: 0, unresolved_usage_request_count: 1,
        known_usage_estimated_cost_usd: 0.25, pricing_complete: true,
        usage_complete: false },
    ], "A")!;
    expect(cost.cost).toBeNull();
    expect(cost.knownCost).toBeCloseTo(0.75);
    expect(cost.pendingCount).toBe(2);
    expect(cost.usageGap).toBe(true);
    expect(cost.pricingMissing).toBe(false);
    expect(cost.requestCount).toBe(7);
  });

  it("returns null for a stage with no usage", () => {
    expect(stageCost([row("A", "", 0.5)], "B")).toBeNull();
    expect(stageCost(undefined, "A")).toBeNull();
  });
});

describe("fmtStageElapsed", () => {
  it("formats minutes and hours", () => {
    expect(fmtStageElapsed(65)).toBe("1:05");
    expect(fmtStageElapsed(3720)).toBe("1h 02m");
  });
});
