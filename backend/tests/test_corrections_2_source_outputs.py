"""Corrections 2.0: source identity survives rendering and semantic ownership."""
import copy

import pytest

from app.services import concept_cleanup, concept_validator, generation
from app.services import generation_quality_policy as quality
from app.services.phase3 import assemble, envelope, host, place, prompts, kernel
from tests import test_phase3_case_uniqueness as cases
from tests import test_phase3_place as placement


def stamped(env, version=quality.V5):
    result = copy.deepcopy(env)
    result["metadata"][quality.KEY] = version
    result["envelope_sha256"] = envelope.seal_sha256(result)
    return result


def test_table_numbers_survive_all_sections_and_validator_without_an_image():
    details = (
        "Description: Interpret Table 3.6 and state its conclusion.\n"
        "Achieving Mastery: Compare the conditions in Table 3.6. // "
        "Types: Type 01: Interpret Data\nCase 01: Compare Conditions\n"
        "Example: Use the observations given in Table 3.3. // "
        "Activity/Info Hub: Activity — Task: Study Table 3.2 along with Fig. 3.11."
    )
    row = {"concept_title": "Tissue Culture", "topic": "Tissues", "concept_details": details, quality.KEY: quality.V5}
    result = concept_cleanup.clean_concept_record(copy.deepcopy(row))
    assert "Table 3.6" in result["concept_details"]
    assert "Table 3.3" in result["concept_details"]
    assert "Table 3.2" in result["concept_details"]
    assert "the given table" not in result["concept_details"]
    assert concept_cleanup.clean_concept_record(copy.deepcopy(result)) == result
    report = concept_validator.validate_concept_rows([result])
    assert not any(f["code"] == "source_artifact" for f in report["errors"])
    legacy = concept_cleanup.clean_concept_record({**row, quality.KEY: quality.V4})
    assert "given in the given table" in legacy["concept_details"]


def test_bound_deposit_retains_tables_without_minting_a_row_stamp():
    row = {"concept_details": "Description: Interpret Table 3.6."}
    with quality.bind_run(quality.V5):
        result = concept_cleanup.clean_concept_record(copy.deepcopy(row))
        assert "Table 3.6" in result["concept_details"]
        assert quality.KEY not in result
        historical = concept_cleanup.clean_concept_record({**row, quality.KEY: quality.V4})
        assert "Table 3.6" not in historical["concept_details"]
    with quality.bind_run(quality.V4):
        assert "Table 3.6" not in concept_cleanup.clean_concept_record(copy.deepcopy(row))["concept_details"]


def test_exact_coverage_distinguishes_different_source_table_numbers():
    with quality.bind_run(quality.V5):
        assert generation._inventory_coverage_key("Use Table 3.3.") != generation._inventory_coverage_key("Use Table 3.6.")
    with quality.bind_run(quality.V4):
        assert generation._inventory_coverage_key("Use Table 3.3.") == generation._inventory_coverage_key("Use Table 3.6.")


def test_v5_keeps_semantic_question_hosts_and_scopes_type_ids_without_loss():
    env = stamped(cases._fanout_envelope())
    before = cases._fanout_hosts()
    result = host.consolidate_type_ownership(env, before, provider=lambda _: pytest.fail("form owner must not replace semantic ownership"))
    assert result["qid_map"] == before["qid_map"]
    assert result["host_map"] == before["host_map"]
    output = assemble.assemble(env, cases._settled_rows(), result)
    rows = output["rows"]
    assert cases.PROMPT_A in rows[0]["concept_details"]
    assert cases.PROMPT_A not in rows[1]["concept_details"]
    assert cases.PROMPT_B in rows[1]["concept_details"]
    assert cases.PROMPT_B not in rows[0]["concept_details"]
    assert "Type 01:" in rows[0]["concept_details"]
    assert "Type 02:" in rows[1]["concept_details"]
    assert all("source-type:TYPE-0001" in route for row in rows for route in row["_aegis_release_type_case_routes"])
    assert not output["coverage"]["case_audit"]
    assert output == assemble.assemble(env, cases._settled_rows(), result)


def test_place_verdict_is_not_overwritten_by_an_answering_form():
    host_result = {**cases._fanout_hosts(), "semantic_case_ownership": quality.V5}
    original = {"hub_placements": {"QINV-0101": "CONCEPT-0002"}, "review_flags": {}}
    assert place.project_type_owner_hub_placements(cases._settled_rows(), host_result, original) == original


def test_activity_instructions_allow_genuine_integration_only_for_new_runs():
    assert "never to a Culmination" in prompts.host_system({})
    current = prompts.host_system({quality.KEY: quality.V5})
    assert "appropriate Culmination" in current
    assert "A textbook Activity, experiment or discussion unit goes to the related NORMAL concept, never to a Culmination" not in current


def test_place_authors_caption_with_source_evidence_and_never_renders_blk_id():
    env = stamped(placement._mini_envelope())
    captured = []
    def provider(payload):
        captured.append(payload)
        verdicts = []
        for item in payload["pool"]:
            verdict = {"item_ref": item["item_ref"], "concept_id": "CONCEPT-0001", "rationale": "The image illustrates the straight path of light."}
            if item["pool_kind"] == "figure":
                verdict["public_caption"] = "Fig. 2 - A pinhole camera forming an inverted image."
            verdicts.append(verdict)
        return {"placements": verdicts}
    result = place.place(env, placement._settled_rows(), provider=provider, critic=lambda _: {"verdict":"verified", "confidence":1.0, "issues":[]}, store=kernel.DecisionStore())
    assert "public_caption" in captured[0]["rules"]
    for block_id, figure in result["figure_pool"].items():
        rendered = generation._figure_hub_note({**figure, "block_id": block_id, "url": figure["urls"][0]})
        assert "BLK-" not in rendered
        assert "Fig. 2" in rendered
    checker = place._place_checker([{"item_ref":"BLK-0001", "pool_kind":"figure"}], {"CONCEPT-0001"}, caption_policy=True)
    assert checker({"placements":[{"item_ref":"BLK-0001", "concept_id":"CONCEPT-0001", "rationale":"visible source", "public_caption":"Figure BLK-0001"}]})


def test_exact_shared_hub_image_is_removed_but_each_question_keeps_its_image():
    tag = '[img src="https://example.org/tissue.png" alt="Fig. 3.11 Epithelial tissue"]'
    hub_tag = tag.replace("Fig. 3.11 Epithelial tissue", "A source-grounded description of the same tissue")
    details = f"Description: Epithelial tissue protects surfaces. // Types: Type 01: Study\nCase 01: Describe\nExample: Describe the tissue. {tag}\nExample: Compare the layers. {tag} // Activity/Info Hub: Figure — Fig. 3.11. {hub_tag}"
    rendered = generation._deduplicate_shared_hub_images(details)
    assert rendered.count(tag) == 2
    assert "Figure — Fig. 3.11." in rendered
    assert rendered == generation._deduplicate_shared_hub_images(rendered)


def test_caption_live_author_and_critic_extend_only_new_systems(monkeypatch):
    systems = []
    monkeypatch.setattr(generation, "_openai_json", lambda system, *_args, **_kwargs: systems.append(system) or {})
    place._live_place({})
    place._live_critic({})
    assert systems == [prompts.PLACE_SYSTEM, prompts.PLACE_CRITIC_SYSTEM]
    place._live_place({quality.KEY: quality.V5})
    place._live_critic({quality.KEY: quality.V5})
    assert "required public_caption" in systems[2]
    assert "public_caption against its supplied" in systems[3]


def test_hub_normalization_preserves_place_and_question_destinations():
    question = "Observe the cell wall and explain its role."
    inventory = {"items": [{"qid":"QINV-1", "source_kind":"activity", "_activity_origin":True, "raw_task":question}]}
    rows = [
        {"concept_title":"Cell Walls", "topic":"Cells", quality.KEY:quality.V5,
         "_aegis_release_qids":["QINV-1"],
         "concept_details":"Description: Cell walls support plant cells. // Types: Type 01: Explain\nCase 01: Support\nExample: " + question},
        {"concept_title":"Culmination - Comparing Cells", "topic":"Cells", quality.KEY:quality.V5,
         "_aegis_hub_placements":["QINV-1"], "concept_details":"Description: Compare the cell structures."},
    ]
    output = generation._normalize_activity_hubs_from_inventory(rows, inventory, {})
    assert "Activity/Info Hub:" not in output[0]["concept_details"]
    assert "Activity/Info Hub:" in output[1]["concept_details"]
    assert question in output[0]["concept_details"]
    assert "Types:" not in output[1]["concept_details"]
    assert not generation._activity_example_hub_alignment_violations(output, inventory)
    assert not generation._hub_inventory_contract_violations(output, inventory)
    assert output == generation._normalize_activity_hubs_from_inventory(copy.deepcopy(output), inventory, {})


def test_shared_hub_asset_projection_remains_inventory_and_replay_stable():
    url = "https://example.org/tissue.png"
    tag = f'[img src="{url}" alt="Fig. 3.11 Epithelial tissue"]'
    row = {"concept_title":"Epithelial Tissue", "topic":"Tissues", quality.KEY:quality.V5,
           "concept_details":f"Description: Epithelial tissue protects surfaces. // Types: Type 01: Observe\nCase 01: Structure\nExample: Describe this tissue. {tag}",
           "_aegis_figure_placements":[{"block_id":"BLK-0001", "url":url, "caption":"Fig. 3.11 Layers of epithelial tissue", "caption_policy":quality.V5}]}
    inventory = {"items": []}
    output = generation._normalize_activity_hubs_from_inventory([row], inventory, {})
    assert output[0]["concept_details"].count(url) == 1
    assert "Fig. 3.11 Layers of epithelial tissue" in output[0]["concept_details"]
    assert not generation._hub_inventory_contract_violations(output, inventory)
    assert output == generation._normalize_activity_hubs_from_inventory(copy.deepcopy(output), inventory, {})
