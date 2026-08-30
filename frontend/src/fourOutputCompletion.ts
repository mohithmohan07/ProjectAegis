import type { SourceArtifactManifest } from "./types";

export type MasterLane = "pre" | "post";

export interface FourOutputCompletion {
  allReady: boolean;
  readyCount: number;
  totalCount: 4;
  missingMasterLanes: MasterLane[];
  missingLabels: string[];
}

const MASTER_LABEL: Record<MasterLane, string> = {
  pre: "Pre-Learning Master File",
  post: "Post-Learning Master File",
};

const OUTPUT_KIND = {
  preConcept: "pre_release_bulk_import",
  preMaster: "pre_release_master",
  postConcept: "release_bulk_import",
  postMaster: "release_master",
} as const;

/**
 * Read the terminal four-output verdict returned by Build Concepts.
 *
 * Older/non-concept streams do not carry this contract and return null, so
 * their historical generic completion behaviour remains unchanged.
 */
export function fourOutputCompletionFromResult(
  data: unknown,
): FourOutputCompletion | null {
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const result = data as Record<string, unknown>;
  if (typeof result.all_four_outputs_ready !== "boolean") return null;

  const outputCompletion = asRecord(result.output_completion);
  const explicitMissing = Array.isArray(outputCompletion?.missing)
    ? outputCompletion.missing
        .map((entry) => asRecord(entry)?.lane)
        .filter((lane): lane is MasterLane => lane === "pre" || lane === "post")
    : [];
  const masterOutputs = asRecord(result.master_outputs);
  const inferredMissing = (["pre", "post"] as MasterLane[]).filter((lane) => {
    const laneResult = asRecord(masterOutputs?.[lane]);
    return laneResult?.ready !== true;
  });
  const missingMasterLanes = explicitMissing.length > 0
    ? explicitMissing
    : inferredMissing;
  const explicitReady = outputCompletion?.ready_count;
  const readyCount = typeof explicitReady === "number"
    && Number.isFinite(explicitReady)
    ? clampOutputCount(explicitReady)
    : 4 - missingMasterLanes.length;

  return {
    allReady: result.all_four_outputs_ready,
    readyCount,
    totalCount: 4,
    missingMasterLanes,
    missingLabels: missingMasterLanes.map((lane) => MASTER_LABEL[lane]),
  };
}

/** Build the same verdict from a refreshed job when a mobile stream detached. */
export function fourOutputCompletionFromManifest(
  manifest?: SourceArtifactManifest,
): FourOutputCompletion | null {
  const files = manifest?.files ?? [];
  const outputKinds = Object.values(OUTPUT_KIND);
  if (!files.some((artifact) => outputKinds.includes(
    artifact.kind as (typeof outputKinds)[number],
  ))) return null;

  const available = (kind: string) => files.some((artifact) =>
    artifact.kind === kind
    && !artifact.disabled
    && Boolean(artifact.download_url));
  const readiness = {
    preConcept: available(OUTPUT_KIND.preConcept),
    preMaster: available(OUTPUT_KIND.preMaster),
    postConcept: available(OUTPUT_KIND.postConcept),
    postMaster: available(OUTPUT_KIND.postMaster),
  };
  const missingMasterLanes: MasterLane[] = [];
  if (!readiness.preMaster) missingMasterLanes.push("pre");
  if (!readiness.postMaster) missingMasterLanes.push("post");
  const readyCount = Object.values(readiness).filter(Boolean).length;

  return {
    allReady: readyCount === 4,
    readyCount,
    totalCount: 4,
    missingMasterLanes,
    missingLabels: missingMasterLanes.map((lane) => MASTER_LABEL[lane]),
  };
}

/** Add the terminal verdict to a result recovered from a refreshed job. */
export function fourOutputResultFields(
  completion: FourOutputCompletion | null,
): Record<string, unknown> {
  if (!completion) return {};
  return {
    all_four_outputs_ready: completion.allReady,
    master_outputs: {
      pre: { ready: !completion.missingMasterLanes.includes("pre") },
      post: { ready: !completion.missingMasterLanes.includes("post") },
    },
    output_completion: {
      ready_count: completion.readyCount,
      total_count: completion.totalCount,
      all_ready: completion.allReady,
      missing: completion.missingMasterLanes.map((lane) => ({
        number: lane === "pre" ? "02" : "04",
        lane,
        label: MASTER_LABEL[lane],
      })),
    },
  };
}

export function incompleteFourOutputLabel(
  completion: FourOutputCompletion,
): string {
  const missing = completion.missingLabels.length > 0
    ? ` (${completion.missingLabels.join(" and ")} unavailable)`
    : "";
  return `Incomplete — ${completion.readyCount}/${completion.totalCount} outputs ready${missing}`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function clampOutputCount(value: number): number {
  return Math.max(0, Math.min(4, Math.trunc(value)));
}
