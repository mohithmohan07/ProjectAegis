"""The reviewer's corrections catalogue, the mechanical half (Q67).

Each test pins one defect the reviewers corrected by hand on Bholi, Print
Culture, How Do Organisms Reproduce and Triangles, traced to its mechanism in
the tree and fixed without moving a judgment into code.
"""
from __future__ import annotations

from app import bulk_import as bi
from app.services import column_spec, generation, release_refiner
from app.services.phase3 import host, prompts


# --------------------------------------------------------------------------- #
# Keywords: one file never mixes delimiters
# --------------------------------------------------------------------------- #

def test_a_pipe_list_with_any_spacing_projects_as_one_list():
    comma = {"keywords_separator": ", "}
    pipe = {}
    for spelling in ("a | b | c", "a |b| c", "a|b|c", " a| b |c "):
        assert column_spec.keyword_cell(spelling, comma) == "a, b, c", spelling
        assert column_spec.keyword_cell(spelling, pipe) == "a | b | c", spelling
    # A comma list stays the one token the writer already treats it as, and
    # exact duplicates collapse without reordering.
    assert column_spec.keyword_cell("a, b", comma) == "a, b"
    assert column_spec.keyword_cell("b | a | b", pipe) == "b | a"


# --------------------------------------------------------------------------- #
# chapter_display_name: one composer for both workbooks
# --------------------------------------------------------------------------- #

def test_the_chapter_display_cell_is_the_human_title_untagged():
    assert bi.chapter_display_cell("Bholi", "Bholi (10_English_CBSE)") == "Bholi"
    assert bi.chapter_display_cell("Bholi (10_English_CBSE)", "") == "Bholi"
    assert bi.chapter_display_cell("", "Tenses (10_Grammar_ICSE)") == "Tenses"
    assert bi.chapter_display_cell("Number System", "Number System") == "Number System"


# --------------------------------------------------------------------------- #
# Activity/Info Hub figure notes
# --------------------------------------------------------------------------- #

def test_a_figure_note_carries_its_caption_once():
    note = generation._figure_hub_note({
        "block_id": "FIG-7-2", "caption": "Figure 7.2 Multiple fission in Plasmodium",
        "url": "https://assets.example.org/fig72.png",
    })
    assert note.count("Multiple fission in Plasmodium") == 2   # once in prose, once as alt
    assert note.startswith("Figure — Figure 7.2 Multiple fission in Plasmodium.")
    assert 'alt="Figure 7.2 Multiple fission in Plasmodium"' in note


def test_a_placeholder_caption_never_ships_as_prose_or_alt():
    for placeholder in ("Source visual", "Source visual 3", "Source figure", ""):
        note = generation._figure_hub_note({
            "block_id": "FIG-7-11", "caption": placeholder,
            "url": "https://assets.example.org/fig711.png",
        })
        assert "Source visual" not in note and "Source figure" not in note, note
        assert note.startswith("Figure — FIG-7-11.")
        assert 'alt="Figure FIG-7-11"' in note


# --------------------------------------------------------------------------- #
# Leading source ordinals
# --------------------------------------------------------------------------- #

def test_a_bare_ordinal_is_removed_only_when_the_provenance_records_it():
    strip = generation._strip_leading_source_task_label
    assert strip("5. What is DNA?", source_label="Q5") == "What is DNA?"
    assert strip("5. What is DNA?", source_label="5.") == "What is DNA?"
    assert strip("(b) Name the parts.", source_label="(b)") == "Name the parts."
    assert strip("iv) Name the parts.", source_label="iv") == "Name the parts."
    # No recorded label, or a different one: the wording is left alone.
    assert strip("5. What is DNA?") == "5. What is DNA?"
    assert strip("5. What is DNA?", source_label="Q15") == "5. What is DNA?"
    assert strip("2 is the smallest prime. Explain.", source_label="Q2") == "2 is the smallest prime. Explain."
    # The textbook labels the old regex already removed still are.
    assert strip("Example 3: Solve for x.", source_label="Example 3") == "Solve for x."
    # Only a standalone label token counts (verification note 16): a label
    # ending in a letter that is not an enumerator never matches a stem.
    assert strip("s. Name the parts.", source_label="Questions") == "s. Name the parts."
    assert strip("e. Name the parts.", source_label="Exercise") == "e. Name the parts."
    assert strip("1. Name the parts.", source_label="Exercise 1.1") == "1. Name the parts."
    assert strip("5. Name the parts.", source_label="Question 5") == "Name the parts."


def test_the_public_example_text_drops_the_recorded_ordinal():
    item = {"raw_task": "5. What is DNA, and what are its uses?", "source_label": "Q5",
            "source_kind": "exercise"}
    assert generation._inventory_task_text(item) == "What is DNA, and what are its uses?"
    item = {"raw_task": "5. What is DNA, and what are its uses?", "source_label": "",
            "source_kind": "exercise"}
    assert generation._inventory_task_text(item) == "5. What is DNA, and what are its uses?"


# --------------------------------------------------------------------------- #
# Type consolidation: the count is not a gate
# --------------------------------------------------------------------------- #

def test_semantic_consolidation_accepts_a_split_that_keeps_every_question(monkeypatch):
    inventory = {"items": [
        {"qid": "QINV-0001", "raw_task": "Explain budding in Hydra.", "topic_hint": "T1"},
        {"qid": "QINV-0002", "raw_task": "Explain spore formation in Rhizopus.", "topic_hint": "T1"},
    ]}

    def case(qid, title):
        return {"case_id": "CASE-0001", "case_title": title, "case_signature": title,
                "examples": [{"source_question_id": qid, "example_prompt": ""}],
                "concept_match_hint": "", "topic_match_hint": "T1", "difficulty_hint": "Basic",
                "cognitive_skill_hint": "", "subject_skill_hint": "", "is_activity": False,
                "placement_scope": "normal"}

    def mined(type_id, title, cases, qids):
        return {"type_id": type_id, "type_title": title, "type_description": title,
                "task_pattern": title, "source_question_ids": qids, "case_prompts": cases}

    original = {"types": [mined("TYPE-0001", "Explaining a Biological Process",
                                [case("QINV-0001", "Budding"), case("QINV-0002", "Spores")],
                                ["QINV-0001", "QINV-0002"])]}
    original["types"].append(mined("TYPE-0002", "Naming an Organism",
                                   [case("QINV-0002", "Spores")], ["QINV-0002"]))
    original["types"][0]["case_prompts"] = [case("QINV-0001", "Budding")]
    original["types"][0]["source_question_ids"] = ["QINV-0001"]
    # The model's answer SPLITS nothing and MERGES nothing here but returns the
    # same two Types in the other order — a candidate with as many Types as the
    # original, accepted before and after. The test then asks for MORE Types.
    split = {"types": [
        mined("TYPE-0001", "Explaining Budding in Hydra", [case("QINV-0001", "Budding")], ["QINV-0001"]),
        mined("TYPE-0002", "Explaining Spore Formation in Rhizopus", [case("QINV-0002", "Spores")], ["QINV-0002"]),
    ]}
    monkeypatch.setattr(generation, "_openai_json", lambda *a, **k: split)
    logged: list[str] = []
    monkeypatch.setattr(generation.progress, "log", lambda message, **k: logged.append(str(message)))

    one_bucket = {"types": [mined("TYPE-0001", "Explaining a Biological Process",
                                  [case("QINV-0001", "Budding"), case("QINV-0002", "Spores")],
                                  ["QINV-0001", "QINV-0002"])]}
    # A single Type is returned as-is (nothing to consolidate) — so present two
    # and let the model answer with two differently scoped ones.
    result = generation._consolidate_semantic_types_via_api(
        original, inventory=inventory, meta={"subject": "Science"})
    assert [t["type_title"] for t in result["types"]] == [
        "Explaining Budding in Hydra", "Explaining Spore Formation in Rhizopus"]
    assert any("accepted" in line for line in logged), logged
    assert not any("Rejected" in line for line in logged), logged
    assert one_bucket  # documented shape the reviewers saw; a split of it is now acceptable


# --------------------------------------------------------------------------- #
# Host receives the miner's hints
# --------------------------------------------------------------------------- #

def test_host_units_carry_the_mining_hints():
    env = {"mined_types": {"types": [{
        "type_id": "TYPE-0001", "type_title": "Proving Similarity", "type_description": "d",
        "owner_topic_id": "T2", "difficulty_hint": "Advanced", "placement_scope": "normal",
        "topic_match_hint": "T2", "cognitive_skill_hint": "Analysing",
        "case_prompts": [{
            "case_id": "CASE-0001", "case_title": "Prove two triangles similar",
            "case_signature": "given angles, prove similarity", "concept_match_hint": "AA criterion",
            "difficulty_hint": "Advanced", "placement_scope": "cross_topic_synthesis",
            "topic_match_hint": "T3", "cognitive_skill_hint": "Applying",
            "examples": [{"source_question_id": "QINV-0009"}],
        }],
    }, {
        "type_id": "TYPE-0002", "type_title": "Caseless", "type_description": "d",
        "owner_topic_id": "T1", "difficulty_hint": "Basic", "source_question_ids": ["QINV-0001"],
    }]}}
    units = host.derive_units(env)
    assert units[0]["difficulty_hint"] == "Advanced"
    assert units[0]["placement_scope"] == "cross_topic_synthesis"
    assert units[0]["topic_match_hint"] == "T3"
    assert units[0]["cognitive_skill_hint"] == "Applying"
    assert units[1]["difficulty_hint"] == "Basic"
    assert units[1]["placement_scope"] == "" and units[1]["topic_match_hint"] == ""


def test_the_host_prompt_places_advanced_work_with_the_later_concept():
    for text in (prompts.HOST_SYSTEM,):
        assert "difficulty_hint is Advanced" in text
        assert "never to a Culmination" in text
        assert "most granular" in text
        # A concept the Host creates carries its mastery line in the same
        # register as every other author's (verification note 8).
        assert "'Achieving Mastery:' line written in the imperative register" in text


# --------------------------------------------------------------------------- #
# The prompts carry the reviewers' rules — additively
# --------------------------------------------------------------------------- #

def test_the_authoring_prompts_state_the_register_voice_and_casing_rules():
    for text in (prompts.ANALYSIS_SYSTEM, prompts.PREMAP_SYSTEM):
        assert "imperative register" in text
        assert "never refer to 'the source'" in text
        # The rule they carried before is still there.
        assert "what a learner can do" in text
    assert "Title Case" in prompts.PREMAP_SYSTEM
    assert "Title Case" in generation.prompts.get_text("concepts.system")
    for text in (prompts.REFINER_SYSTEM, release_refiner._RULES):
        assert "dangling connector" in text and "imperative" in text
        assert "You may reword ONLY the Description prose" in text or "Edit ONLY the Description prose" in text
    for text in (prompts.CRITIC_SYSTEM, prompts.POLISH_CRITIC_SYSTEM):
        assert "not in the imperative register" in text
    mining = generation.prompts.get_text("concepts.type_mining.system")
    assert "never share a case_title" in mining
    assert "share one Case (a Case can hold several Examples)" in mining
    assert "never begins with the source's question number" in mining
    inventory = generation.prompts.get_text("concepts.question_task_inventory.system")
    assert "provenance, not wording" in inventory
    assert "raw_task must carry the COMPLETE question wording verbatim" in inventory
    recovery = generation.prompts.get_text("concepts.opening_recovery.system")
    assert 'separated by exactly\n  " | "' in recovery or 'separated by exactly' in recovery


def test_the_settle_critic_flags_forward_references_to_a_later_topic():
    """Q69: the critic is told to flag an explanation that leans on a term
    the chapter first introduces in a later topic, guarded by the evidence
    being present so the topology and grounding stages it serves are
    untouched."""
    from app.services.phase3 import prompts

    assert "chapter_topics_in_teaching_order" in prompts.CRITIC_SYSTEM
    assert "first introduces in a LATER topic" in prompts.CRITIC_SYSTEM
    assert "name that later topic" in prompts.CRITIC_SYSTEM
    assert "When the request carries chapter_topics_in_teaching_order" in prompts.CRITIC_SYSTEM



# --------------------------------------------------------------------------- #
# The owner's delimiter decision, 14 September 2026: keywords are a pipe list
# --------------------------------------------------------------------------- #

def test_a_new_run_writes_keywords_as_a_pipe_list_in_every_subject():
    """Contract §16 and Appendix B.1 always said ``" | "``; Q33 had made the
    cell comma-space and the reviewers re-delimited three chapters by hand.
    The owner settled it on the contract's side for new runs."""
    for subject in ("English", "Mathematics", "Science", "Social Science"):
        policy = column_spec.for_metadata({"subject": subject})
        assert policy["keywords_separator"] == " | ", subject
        assert policy["version"] == column_spec.VERSION


def test_a_comma_typed_cell_re_delimits_under_the_pipe_policy():
    """A person typing the old spelling must not silently become ONE keyword:
    under the pipe policy a comma list is read as the pre-v2.0 list it is."""
    pipe = column_spec.for_metadata({"subject": "Mathematics"})
    assert column_spec.keyword_cell("median, class interval, mode", pipe) == (
        "median | class interval | mode"
    )
    # Every pipe spelling still lands on the one canonical form.
    for spelling in ("a | b | c", "a |b| c", "a|b|c", " a| b |c "):
        assert column_spec.keyword_cell(spelling, pipe) == "a | b | c", spelling
    # And the cell the writer produces passes its own read-back check.
    assert column_spec.keyword_defects("median | class interval | mode", pipe) == []


def test_a_profile_frozen_under_the_comma_policy_still_writes_commas():
    """Identity: a sealed run carries its own policy dict, so the delimiter
    change cannot re-render an already-published workbook."""
    frozen = {"keywords_separator": ", ", "version": "owner-column-spec-2026-09-08-v2"}
    assert column_spec.keyword_cell("a | b | c", frozen) == "a, b, c"
    assert column_spec.keyword_cell("a, b, c", frozen) == "a, b, c"
