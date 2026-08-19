"""Step 12 — staging acceptance corpus (docs/spec-step12.md D1–D4).

Ten-axis variety corpus, identity-locked. Every assertion is mechanics:
sha256 identity on frozen bytes, exact-once closure, contiguous QINV
numbering, flags-surface-as-flags, run-completes. The scripted providers
ARE the model verdicts; no assertion decides what a source means.

The two English cases double as the corpus's QX demonstration: their
question sections sit under real Indian-textbook headings ("Comprehension",
"Working with the text") that the retired cue vocabularies never knew, so
the candidate-bound echo author yields ZERO tasks — the measured 24→0
class — while an ask-aware scripted author recovers them through the same
compile path with closed-world accounting. Same bytes, different authority:
the proof the authority moved to the model.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase212 as qx
from app.services import canonical_source_phase3 as phase3
from tests.conftest import convert_concept_upload, stream_result


SOURCES = Path(__file__).parent / "acceptance_corpus" / "sources"

# Identity locks measured on the frozen sources through the REAL active
# compile path (Phase 2 → 2.1 → 2.1.2 echo adjudication → semantic graph).
# Reviewed when a deliberate compiler change moves them — never "refreshed"
# to make a red test green without reading the diff.
CORPUS = {
    "mh3_evs_thin.mmd": {
        "raw_sha256": "f0a596ceb08078299f800aa4b410cebb72b10de55786cb55db86583b1a321c59",
        "source_contract_hash": "2b657fff035f50c4bb3953d3df4e2c5c35c04e3c16759cd204a795f5ffa44e4c",
        "metadata": {"board": "MSBSHSE", "grade": "3", "subject": "EVS",
                     "chapter_title": "Our Family, Our Neighbourhood"},
        "topics": 3, "tasks": 3, "blocks": 19, "status": "ready",
    },
    "mh4_maths_short.mmd": {
        "raw_sha256": "efcd8d21dcb27ccfb9435ed8ff5fdc3c797cab36d6b784026fcc7fddd26118d5",
        "source_contract_hash": "633b892e0a85eaab36f3fbfc5bd4e4e9ce3bc7d453d826545d62d544413db2e8",
        "metadata": {"board": "MSBSHSE", "grade": "4",
                     "subject": "Mathematics",
                     "chapter_title": "Multiplication"},
        "topics": 2, "tasks": 6, "blocks": 33, "status": "ready",
    },
    "cbse6_science_activities.mmd": {
        "raw_sha256": "9e129efea0d183fbbedfd64475155afa3c08f72f137edf2aa2e8398cd40f1e4c",
        "source_contract_hash": "5071605eb8b57119c4ec9f9c249aa23ac44a5ca9b142ee07fda358a8c0f9e360",
        "metadata": {"board": "CBSE", "grade": "6", "subject": "Science",
                     "chapter_title": "Getting to Know Plants"},
        "topics": 3, "tasks": 7, "blocks": 39, "status": "ready",
    },
    "cbse8_maths_katex.mmd": {
        "raw_sha256": "ef5842ae0f7b78570949be88a62e06506c873b7db59ac5c78557361e79d5d06f",
        "source_contract_hash": "a9cb6a4e127b4a260e4115bb45695a24d68cc6060dd91fa74399d0eccf7267b0",
        "metadata": {"board": "CBSE", "grade": "8", "subject": "Mathematics",
                     "chapter_title": "Exponents and Powers"},
        # The KaTeX-dense case ships review_required at the graph layer;
        # recorded as-is (the flag SURFACES, the compile completes).
        "topics": 3, "tasks": 7, "blocks": 45, "status": "review_required",
    },
    "icse10_english_poem.mmd": {
        "raw_sha256": "eefe5b403f56a2bb26a8ceb7188e6884621f78eabfa0dd88ebfc1d943e86320f",
        "source_contract_hash": "9d7d8e9e1e5a776d5bc1c5a832fe28d908c2d974e4c1036247331b5ddcb57a22",
        "metadata": {"board": "ICSE", "grade": "10", "subject": "English",
                     "chapter_title": "The Lantern on the Hill"},
        # Candidate-bound echo author: the parser knows none of the poem's
        # question headings, so ZERO candidates exist. The recovery twin
        # below proves the same bytes carry askable questions.
        "topics": 4, "tasks": 0, "blocks": 25, "status": "ready",
    },
    "cbse7_english_prose.mmd": {
        "raw_sha256": "138776ff36cd72fb3dbbba9f344f9c5ebbb9c8734181ec37808bd5c92ba4bd4a",
        "source_contract_hash": "ca2376f6b161d8c222c38ad739da7835b4cb87448def9f5664863ee532c07f1d",
        "metadata": {"board": "CBSE", "grade": "7", "subject": "English",
                     "chapter_title": "The Kite Maker's Morning"},
        "topics": 3, "tasks": 0, "blocks": 33, "status": "ready",
    },
    "kseeb9_socsci_hubs.mmd": {
        "raw_sha256": "f6202dfc9684b6950a30a9b13988566be7b4081da01be9bbdf255e46c1b60f2f",
        "source_contract_hash": "6e744c5eca69d03d4482cfe2c770898dfa621ce2f7e3f97582a43e293a346877",
        "metadata": {"board": "KSEEB", "grade": "9",
                     "subject": "Social Science",
                     "chapter_title": "Rivers and Settlements"},
        "topics": 3, "tasks": 5, "blocks": 37, "status": "ready",
    },
}

# The English cases' question sections, by heading. Used by the ask-aware
# scripted author (the test's model stand-in) to rule those blocks.
ENGLISH_QUESTION_CASES = {
    "icse10_english_poem.mmd": ("Comprehension", "Appreciation"),
    "cbse7_english_prose.mmd": ("Working with the text",
                                "Working with words"),
}


def _source(name: str) -> str:
    return (SOURCES / name).read_text(encoding="utf-8")


def _compiled(name: str):
    return phase2.compile_phase2_source(
        _source(name),
        source_filename=name,
        consumer_module="build_concepts",
    )


def test_manifest_names_every_corpus_source_and_locks_identity():
    checked_in = {path.name for path in SOURCES.glob("*.mmd")}
    assert checked_in == set(CORPUS)
    assert len(CORPUS) == 7
    assert len({row["raw_sha256"] for row in CORPUS.values()}) == len(CORPUS)
    for filename, expected in CORPUS.items():
        raw = (SOURCES / filename).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected["raw_sha256"]


@pytest.mark.parametrize("filename", CORPUS)
def test_case_rebuilds_exact_grounding_preconditions(filename, tmp_path,
                                                     monkeypatch):
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "qx-cache")
    expected = CORPUS[filename]
    compiled = _compiled(filename)
    canonical = compiled.canonical
    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=_source(filename),
        metadata=expected["metadata"],
    )

    assert canonical["phase2_inventory_ready"] is True
    assert compiled.report["phase2_issues"] == []
    assert graph["source_contract_hash"] == expected["source_contract_hash"]
    assert len(graph["topics"]) == expected["topics"]
    assert len(graph["tasks"]) == expected["tasks"]
    assert len(graph["blocks"]) == expected["blocks"]
    assert graph["status"] == expected["status"]

    # Exact-once closure (R4): block identity canonical↔graph, task-id
    # closure, contiguous QINV numbering, closed-world QX accounting.
    canonical_blocks = {row["block_id"] for row in canonical["blocks"]}
    graph_blocks = {row["block_id"] for row in graph["blocks"]}
    assert graph_blocks == canonical_blocks
    qids = [task["qid"] for task in canonical.get("tasks") or []]
    assert qids == [f"QINV-{i:04d}" for i in range(1, len(qids) + 1)]
    ledger = canonical.get("task_verdict_ledger") or {}
    accounting = ledger.get("accounting") or {}
    assert accounting.get("unaccounted_block_ids") == []
    assert canonical["task_membership"]["authority"] == "model_verdict"


@pytest.mark.parametrize("filename", sorted(ENGLISH_QUESTION_CASES))
def test_english_questions_recover_under_an_ask_aware_author(
    filename, tmp_path, monkeypatch
):
    """Same bytes, different authority: the cue-blind echo yields 0 tasks
    (pinned above); an author that reads the blocks recovers every printed
    question section with closed-world accounting and durable flags."""
    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "qx-cache")
    headings = ENGLISH_QUESTION_CASES[filename]

    def ask_aware(*, system, prompt, schema, purpose="source_extraction",
                  max_tokens=None):
        payload = json.loads(prompt)
        if system == qx._CRITIC_SYSTEM:
            return {"verdict": "concur", "dissents": []}
        if system == qx._FIXER_SYSTEM:  # pragma: no cover - not expected
            block = payload["block"]
            return {
                "block_id": block["block_id"], "verdict": "not_task",
                "confirmed_candidate_ids": [], "rejected_candidate_ids": [],
                "missed_asks": [], "continues_task_ref": "",
                "context_for_task_refs": [], "rationale": "unused",
            }
        out = []
        counter = 0
        for blk in payload.get("blocks") or []:
            entry = {
                "block_id": blk["block_id"], "verdict": "not_task",
                "confirmed_candidate_ids": [], "rejected_candidate_ids": [],
                "missed_asks": [], "continues_task_ref": "",
                "context_for_task_refs": [],
            }
            text = blk["text"]
            # The scripted verdict (the test's model stand-in): numbered
            # items in this chapter's exercise sections are learner asks.
            # The numbering split is the script's own transcription device,
            # not product code; evidence is quoted verbatim from the block.
            starts = list(re.finditer(r"^\d+\.\s+", text, re.MULTILINE))
            if starts and blk.get("kind") != "heading":
                counter += 1
                entry["verdict"] = "contains_tasks"
                for i, match in enumerate(starts, start=1):
                    begin = match.end()
                    nxt = re.search(
                        r"^\d+\.\s+", text[begin:], re.MULTILINE
                    )
                    end = begin + (nxt.start() if nxt else len(text[begin:]))
                    entry["missed_asks"].append({
                        "evidence_text": text[begin:end].strip(),
                        "task_ref": f"ASK-{counter}-{i}",
                    })
            out.append(entry)
        return {"blocks": out}

    monkeypatch.setattr(qx, "_call_provider", ask_aware)
    monkeypatch.setattr(qx, "_provider_ready", lambda: True)
    canonical = _compiled(filename).canonical

    tasks = canonical.get("tasks") or []
    assert len(tasks) >= 4, "the printed questions must enter the inventory"
    accounting = canonical["task_verdict_ledger"]["accounting"]
    assert accounting["unaccounted_block_ids"] == []
    assert accounting["created_from_missed_asks"] == len(tasks)
    assert all(
        t["membership_authority"] == "model_verdict" for t in tasks
    )
    # Source fidelity: recovered wording is verbatim from the source.
    source = _source(filename)
    for task in tasks:
        assert task["raw_prompt"] in source
    # The section headings themselves never became tasks.
    for heading in headings:
        assert not any(heading in t["raw_prompt"] for t in tasks)


@pytest.mark.parametrize("filename", CORPUS)
def test_http_drive_completes_for_every_case(
    filename, client, first_chapter,
):
    """D4 item 4: the production HTTP lane (upload → convert → generate)
    returns a completed result for every corpus case — no stream error, no
    halt. Concept counts are the dry stub's business, deliberately
    unpinned."""
    body = _source(filename).encode("utf-8")
    files = {"file": (filename, io.BytesIO(body), "text/markdown")}
    job = client.post(
        "/build-concepts/post-learning/uploads", files=files
    ).json()
    assert job["status"] == "uploaded"
    converted = convert_concept_upload(client, job["id"])
    assert converted["status"] == "converted"
    result = stream_result(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]},
    ))
    assert "error" not in result, result
