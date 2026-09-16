import {expect, test} from "vitest";
import {lineText} from "./ChapterRowDrawer";

test("a result event does not make a failed generation look finished", () => {
  expect(lineText({type: "result", data: {run_incomplete: {
    resume_allowed: true, automatic_retry_allowed: false,
    recovery_action: "restore_source_evidence",
  }}})).toContain("restore and verify source evidence");
  expect(lineText({type: "result", data: {run_incomplete: {
    resume_allowed: false, recovery_action: "reconvert_new_upload",
  }}})).toContain("checkpoint cannot resume");
});

test("batch waiting, review readiness and complete outputs have distinct outcomes", () => {
  expect(lineText({type: "result", data: {waiting: true, reason: "batch_wait"}})).toContain("resume automatically");
  expect(lineText({type: "result", data: {concept_review: {status: "pending_review"}}})).toContain("ready for review");
  expect(lineText({type: "result", data: {all_four_outputs_ready: true}})).toContain("all four outputs are ready");
  expect(lineText({type: "result", data: {all_four_outputs_ready: false}})).toContain("stopped incomplete");
});
