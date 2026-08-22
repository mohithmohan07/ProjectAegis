import type { RunLine } from "../RunConsole";
import type { StageUsageRow } from "../types";

/** One rendered stage card: a step line plus everything until the next step. */
export interface StageGroup {
  title: string;
  startTs: number;
  /** Timestamp of the last event seen in this stage (running stages tick live). */
  endTs: number;
  lines: RunLine[];
  /** Distinct lanes (parallel tracks) that spoke inside this stage, in order. */
  lanes: string[];
  hasError: boolean;
  hasWarning: boolean;
}

/** Aggregated usage for one stage across its lanes, plus the per-lane rows. */
export interface StageCost {
  requestCount: number;
  totalTokens: number;
  cachedInputTokens: number;
  cacheWriteTokens: number;
  cost: number | null;
  costComplete: boolean;
  lanes: StageUsageRow[];
}

/**
 * Partition the console's line list into stage groups: every `step` line
 * starts a group and owns everything until the next one. Lines before the
 * first step land in a leading "Starting" group only when they exist.
 * Pure and total — the raw view stays available unchanged beside it.
 */
export function groupStages(lines: RunLine[]): StageGroup[] {
  const groups: StageGroup[] = [];
  let current: StageGroup | null = null;
  const open = (title: string, ts: number): StageGroup => {
    const group: StageGroup = {
      title, startTs: ts, endTs: ts, lines: [], lanes: [],
      hasError: false, hasWarning: false,
    };
    groups.push(group);
    return group;
  };
  for (const line of lines) {
    if (line.level === "step") {
      current = open(line.message, line.ts);
      continue;
    }
    if (!current) current = open("Starting", line.ts);
    current.lines.push(line);
    current.endTs = Math.max(current.endTs, line.ts);
    if (line.lane && !current.lanes.includes(line.lane)) {
      current.lanes.push(line.lane);
    }
    if (line.level === "error") current.hasError = true;
    if (line.level === "warn" || line.level === "warning") {
      current.hasWarning = true;
    }
  }
  return groups;
}

/**
 * Stage-usage rows are scoped to the current server attempt, while the
 * console deliberately keeps log cards from an attempt that was resumed.
 * The accumulator groups rows by stage title, so it cannot truthfully split
 * one title's total across two same-title cards.  Attach it only to the
 * newest occurrence; older cards remain useful history without claiming the
 * current attempt's tokens or cost.
 */
export function latestStageOccurrenceIndexes(
  groups: readonly StageGroup[],
): Set<number> {
  const newestByTitle = new Map<string, number>();
  groups.forEach((group, index) => newestByTitle.set(group.title, index));
  return new Set(newestByTitle.values());
}

/**
 * Sum the per-(stage, lane) usage rows for one stage title. Cost is null
 * (with costComplete=false) as soon as any contributing row is unpriced,
 * so a partial number is never shown as a total.
 */
export function stageCost(
  rows: StageUsageRow[] | undefined,
  stageTitle: string,
): StageCost | null {
  const mine = (rows ?? []).filter((row) => row.stage === stageTitle);
  if (mine.length === 0) return null;
  let cost = 0;
  let costComplete = true;
  let requestCount = 0;
  let totalTokens = 0;
  let cachedInputTokens = 0;
  let cacheWriteTokens = 0;
  for (const row of mine) {
    requestCount += row.request_count;
    totalTokens += row.total_tokens;
    cachedInputTokens += row.cached_input_tokens ?? 0;
    cacheWriteTokens += row.cache_write_tokens ?? 0;
    if (!row.pricing_complete || row.estimated_cost_usd == null) {
      costComplete = false;
    } else {
      cost += row.estimated_cost_usd;
    }
  }
  return {
    requestCount,
    totalTokens,
    cachedInputTokens,
    cacheWriteTokens,
    cost: costComplete ? cost : null,
    costComplete,
    lanes: mine.filter((row) => row.lane !== ""),
  };
}

/** m:ss / h mm elapsed formatting shared by the stage cards. */
export function fmtStageElapsed(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const m = Math.floor(whole / 60);
  const s = whole % 60;
  if (m >= 60) return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
  return `${m}:${String(s).padStart(2, "0")}`;
}
