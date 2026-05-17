"""Content generation: questions from concepts, concepts from MMD.

All functions have a dry path (deterministic, no API keys — used for the MVP
and tests) and a live hook that delegates to the vendored OpenAI-backed
scripts. The dry path is intentionally realistic: it returns fully-populated
records so the post-generation pipeline and the canonical writer are always
exercised end to end.
"""
from __future__ import annotations

import re

from .. import config, models

_SLUG_RE = re.compile(r"[^A-Za-z0-9]")


def _slug(text: str, length: int = 22) -> str:
    return _SLUG_RE.sub("", (text or "").title())[:length] or "X"


def _topic_index(concept: models.Concept) -> int:
    topic = concept.topic
    siblings = sorted(topic.chapter.topics, key=lambda t: t.id)
    return siblings.index(topic) + 1


def question_label(concept: models.Concept, n: int) -> str:
    """Build a canonical question label, e.g. 10CBMA_Crcls_PL_T01_CncptlMnng Q03."""
    ch = concept.topic.chapter
    prefix = ch.chapter_code.split("_")[0] if ch.chapter_code else _slug(ch.chapter_title, 6)
    return (
        f"{prefix}_{_slug(ch.chapter_title, 6)}_PL_"
        f"T{_topic_index(concept):02d}_{_slug(concept.concept_title)} Q{n:02d}"
    )


# --------------------------------------------------------------------------- #
# Questions from concepts (Build Assessments - concept mapping path)
# --------------------------------------------------------------------------- #

def _objective_answers(concept: models.Concept) -> list[dict]:
    return [
        {"answer_type": "Words", "answer_content": f"{concept.concept_title} (correct)",
         "correct_answer": "Yes", "answer_weightage": "1"},
        {"answer_type": "Words", "answer_content": "Plausible distractor A",
         "correct_answer": "No", "answer_weightage": "0"},
        {"answer_type": "Words", "answer_content": "Plausible distractor B",
         "correct_answer": "No", "answer_weightage": "0"},
        {"answer_type": "Words", "answer_content": "Plausible distractor C",
         "correct_answer": "No", "answer_weightage": "0"},
    ]


def _subjective_answers(concept: models.Concept, marks: float) -> list[dict]:
    return [
        {"answer_type": "Words", "answer": concept.concept_title,
         "answer_display": "Yes", "weightage": str(marks), "placeholder": "answer"},
    ]


def _descriptive_answers(concept: models.Concept, marks: float) -> tuple[list[dict], list[dict]]:
    answers = [
        {"answer_type": "Words", "answer_weightage": str(marks),
         "answer_content": f"Model answer covering {concept.concept_title}."},
    ]
    sub = [
        {"text": f"i. Define {concept.concept_title}.", "marks": "2",
         "keywords": [{"answer_type": "Words", "weightage": "2",
                       "keyword": concept.concept_title}]},
        {"text": f"ii. Apply {concept.concept_title} to a worked example.",
         "marks": str(max(marks - 2, 1)),
         "keywords": [{"answer_type": "Words", "weightage": str(max(marks - 2, 1)),
                       "keyword": "worked example"}]},
    ]
    return answers, sub


def _default_marks(kind: str) -> float:
    return {"objective": 1, "subjective": 3, "descriptive": 5}[kind]


def generate_questions_for_concept(
    concept: models.Concept,
    *,
    question_type: str,
    cognitive_skill: str,
    difficulty: str,
    category: str,
    count: int,
    start_index: int = 1,
    live: bool | None = None,
) -> list[dict]:
    """Return ``count`` question dicts for one concept under one blueprint cell."""
    use_live = config.use_live_generation() if live is None else live
    if use_live:
        from aegis_pipeline import bulk_upload_ultimate  # noqa: F401
        raise NotImplementedError(
            "Live question generation: wire bulk_upload_ultimate's GPT parsing/"
            "generation with OPENAI_API_KEY and the concept_details payload."
        )

    marks = _default_marks(question_type)
    out: list[dict] = []
    for i in range(count):
        idx = start_index + i
        record: dict = {
            "sheet_kind": question_type,
            "question_label": question_label(concept, idx),
            "question_category": category,
            "cognitive_skills": cognitive_skill,
            "question_source": "Aegis Concept Mapping",
            "level_of_difficulty": difficulty,
            "marks": marks,
            "question": (
                f"[{difficulty} · {cognitive_skill}] "
                f"{category} on '{concept.concept_title}': "
                f"{concept.concept_details.split('//')[0].strip()[:160]}"
            ),
            "answer_explanation": f"Assesses {concept.concept_title} ({cognitive_skill}).",
            "answers": [],
            "sub_questions": [],
            "origin": "concept_mapping",
        }
        if question_type == "objective":
            record["answers"] = _objective_answers(concept)
        elif question_type == "subjective":
            record["answers"] = _subjective_answers(concept, marks)
            record["math_keyboard"] = "Yes" if concept.topic.chapter.subject in {
                "Mathematics", "Physics", "Chemistry"} else ""
        else:
            answers, sub = _descriptive_answers(concept, marks)
            record["answers"] = answers
            record["sub_questions"] = sub
            record["display_answer"] = "Yes"
        out.append(record)
    return out


# --------------------------------------------------------------------------- #
# Questions identified from an uploaded document (Build Assessments - upload path)
# --------------------------------------------------------------------------- #

def identify_questions_from_mmd(
    mmd_text: str, *, upload_type: str, question_type: str = "objective",
    live: bool | None = None,
) -> list[dict]:
    """Extract / create question records from an uploaded document's MMD."""
    use_live = config.use_live_generation() if live is None else live
    if use_live:
        from aegis_pipeline import bulk_upload_mathpix  # noqa: F401
        raise NotImplementedError(
            "Live extraction: wire bulk_upload_mathpix parsing with OPENAI_API_KEY."
        )
    # Dry: split the MMD body into question-like chunks.
    chunks = [c.strip() for c in re.split(r"\n\s*\n+", mmd_text) if c.strip()]
    chunks = [c for c in chunks if not c.startswith("#")] or ["(no question content detected)"]
    records: list[dict] = []
    for i, chunk in enumerate(chunks[:25], start=1):
        rec = {
            "sheet_kind": question_type,
            "question_category": "Multiple Choice Question" if question_type == "objective"
            else "Short Answer" if question_type == "subjective" else "Long Answer",
            "cognitive_skills": "Understanding",
            "question_source": f"Upload · {upload_type}",
            "level_of_difficulty": "Moderate",
            "marks": _default_marks(question_type),
            "question": chunk[:400],
            "answer_explanation": "",
            "answers": [],
            "sub_questions": [],
            "origin": "upload",
        }
        if upload_type in {"questions_and_answers", "textbook"} and question_type == "objective":
            rec["answers"] = [
                {"answer_type": "Words", "answer_content": "Extracted option",
                 "correct_answer": "Yes", "answer_weightage": "1"},
            ]
        records.append(rec)
    return records


# --------------------------------------------------------------------------- #
# Concepts from MMD (Build Concepts - post learning)
# --------------------------------------------------------------------------- #

def concepts_from_mmd(mmd_text: str, *, live: bool | None = None) -> list[dict]:
    """Parse an MMD document into concept records (post-learning)."""
    use_live = config.use_live_generation() if live is None else live
    if use_live:
        from aegis_pipeline import mmd_to_concepts_excel  # noqa: F401
        raise NotImplementedError(
            "Live concept extraction: wire mmd_to_concepts_excel.process_mmd_file "
            "with OPENAI_API_KEY."
        )
    # Dry: treat markdown headings as topics and bullet/para lines as concepts.
    topic = "Topic 01: Overview"
    out: list[dict] = []
    for line in mmd_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("##"):
            topic = line.lstrip("# ").strip() or topic
        elif line.startswith("#"):
            continue
        else:
            title = line.split(":")[0].split(".")[0].strip()[:80] or "Concept"
            out.append({
                "topic": topic,
                "concept_title": title,
                "concept_details": (
                    f"Description: {line[:200]} "
                    "// Types: Type 01: Standard "
                    "// Misconception: commonly confused with related ideas."
                ),
                "keywords": ", ".join(title.lower().split()[:5]),
            })
    return out or [{
        "topic": topic, "concept_title": "Concept 01",
        "concept_details": "Description: (empty document) // Types: // Misconception:",
        "keywords": "",
    }]


def pre_learning_from_concepts(concepts: list[models.Concept], *, live: bool | None = None) -> list[dict]:
    """Derive pre-learning concept records from existing post-learning concepts."""
    use_live = config.use_live_generation() if live is None else live
    if use_live:
        from aegis_pipeline import concept_mapping_to_prelearning  # noqa: F401
        raise NotImplementedError(
            "Live pre-learning: wire concept_mapping_to_prelearning with OPENAI_API_KEY."
        )
    out: list[dict] = []
    for c in concepts:
        out.append({
            "source_concept_id": c.id,
            "topic": f"{c.topic.topic_title} (Pre-Learning)",
            "concept_title": f"Pre: {c.concept_title}",
            "concept_details": (
                f"Description: foundational idea required before learning "
                f"'{c.concept_title}'. // Types: Type 01: Prerequisite recall "
                "// Misconception: assuming the prerequisite is already mastered."
            ),
            "keywords": c.keywords,
        })
    return out
