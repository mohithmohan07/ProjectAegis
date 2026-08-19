# Residue ledger — build-sprint (running, per owner guardrail 29)

One line per unresolved residue. Severity: does downstream work build on a
false contract if this stays open? None below is foundation-false; the
sprint continues past all of them.

| # | Residue | Location | Consequence | Downstream safe? | Owner / fix point |
|---|---|---|---|---|---|
| R-QX1 | Legacy API extractor's post-spend semantic raises not re-polarized (`generation.py:6935-6939,7458-7466,7516-7535`) | legacy `_extract_question_task_inventory_via_api` body | Production-unreachable (Phase 2 rebind); the offline sample script and no-canonical paths can still halt on semantic doubt | Yes | QX follow-up slice |
| R-QX2 | QX-created tasks (missed asks) skip the Phase 2.1 visual-ownership pass — recorded in the ledger's `recorded_limits` | `canonical_source_phase212.py` reconciliation | An image-bearing ask recovered by the author ships without its visual until re-compiled with visuals rerun | Yes (flagged, recorded) | QX follow-up; re-order visuals after adjudication |
| R-QX3 | Live acceptance (QX4) not run — no real-book validation of author/critic/Fixer prompts | docs/spec-qx.md §6 | Semantic quality on real chapters unproven; scripted tests prove mechanics only | Yes for build; NO for production sign-off | Owner: live key + acceptance corpus run |
| R-QX4 | QX-created tasks default `source_kind="checkpoint_question"` with `not_model_ruled_flagged` (membership model-ruled, KIND not) | `_created_task` | Kind-sensitive routing treats recovered asks as checkpoint questions until a kind authority rules | Yes (flagged) | Kind-ruling step (outline-equivalent for text) |
| R-QX5 | `_refresh_inventory_from_source_anchors` and `_extract_question_task_inventory_via_api` names are stale (they render from the adjudicated ledger) | `canonical_source_phase2_contract.py`, `generation.py` | Misleading names only; behavior truthful, logs updated | Yes | Naming-truth cleanup |
| R-QX6 | Suite-wide echo author (conftest autouse) is a test-harness membership authority; any future test asserting the ABSENCE of QX fields will fight it | `tests/conftest.py` | Test-harness convention, not production; recorded so nobody mistakes echo verdicts for model output | Yes | Test harness; revisit at final debug pass |
| R-QX7 | Direct `_source_task_anchors` pins (corpus counts, ~30 anchor tests) not yet re-commented as candidate-surface pins | `tests/test_review_corpus_contracts.py` et al. | Comment debt only; the tests are mechanically correct as candidate pins | Yes | Final debug/cleanup pass |
| R-QX8 | Oversized single blocks are not windowed; the whole block text goes to the author in one payload | `canonical_source_phase212._author_prompt` | A pathologically large block could overrun the request; correction/Fixer path catches the failure loudly | Yes | QX follow-up (lossless windowing per spec §4) |
