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
