import { expect, test } from "vitest";
import {
  fourOutputCompletionFromManifest,
  fourOutputCompletionFromResult,
  fourOutputResultFields,
} from "./fourOutputCompletion";
import type { SourceArtifactManifest } from "./types";

test("reads the server's explicit 3/4 output verdict", () => {
  const completion = fourOutputCompletionFromResult({
    all_four_outputs_ready: false,
    master_outputs: {
      pre: { ready: true },
      post: { ready: false },
    },
    output_completion: {
      ready_count: 3,
      missing: [{ lane: "post", label: "Post-Learning Master File" }],
    },
  });

  expect(completion).toEqual({
    allReady: false,
    readyCount: 3,
    totalCount: 4,
    missingMasterLanes: ["post"],
    missingLabels: ["Post-Learning Master File"],
  });
});

test("reconstructs four-output completion from a refreshed mobile job", () => {
  const manifest = {
    files: [
      artifact("pre_release_bulk_import", true),
      artifact("pre_release_master", true),
      artifact("release_bulk_import", true),
      artifact("release_master", false),
    ],
  } as SourceArtifactManifest;

  const completion = fourOutputCompletionFromManifest(manifest);
  expect(completion?.readyCount).toBe(3);
  expect(completion?.allReady).toBe(false);
  expect(completion?.missingMasterLanes).toEqual(["post"]);
  expect(fourOutputResultFields(completion)).toMatchObject({
    all_four_outputs_ready: false,
    master_outputs: {
      pre: { ready: true },
      post: { ready: false },
    },
  });
});

function artifact(kind: string, ready: boolean) {
  return {
    kind,
    label: kind,
    filename: ready ? `${kind}.xlsx` : "",
    media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    size_bytes: ready ? 1 : 0,
    download_url: ready ? `/downloads/${kind}` : "",
    disabled: !ready,
  };
}
