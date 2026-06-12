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
from . import katex_rules as kr

_SLUG_RE = re.compile(r"[^A-Za-z0-9]")
_MATH_SUBJECTS = {"Mathematics", "Physics", "Chemistry"}


def _is_math(concept: models.Concept) -> bool:
    return concept.topic.chapter.subject in _MATH_SUBJECTS


def _sample_equation(concept: models.Concept) -> str:
    """A representative [katex] expression that references the concept."""
    name = _slug(concept.concept_title, 16) or "X"
    return kr.katex(rf"\text{{{name}}} = f(x)")


def _concept_reference_link(concept: models.Concept) -> str:
    """Link to a public reference for the concept (Wikipedia search)."""
    from urllib.parse import quote_plus
    q = quote_plus(f"{concept.concept_title} {concept.topic.chapter.subject or ''}".strip())
    return kr.link(concept.concept_title, f"https://en.wikipedia.org/wiki/Special:Search?search={q}")


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
    correct = f"{concept.concept_title} (correct)"
    if _is_math(concept):
        correct = f"{correct} — {_sample_equation(concept)}"
    return [
        {"answer_type": "Words", "answer_content": correct,
         "correct_answer": "Yes", "answer_weightage": "1"},
        {"answer_type": "Words", "answer_content": "Plausible distractor A",
         "correct_answer": "No", "answer_weightage": "0"},
        {"answer_type": "Words", "answer_content": "Plausible distractor B",
         "correct_answer": "No", "answer_weightage": "0"},
        {"answer_type": "Words", "answer_content": "Plausible distractor C",
         "correct_answer": "No", "answer_weightage": "0"},
    ]


def _subjective_answers(concept: models.Concept, marks: float) -> list[dict]:
    ans = concept.concept_title
    if _is_math(concept):
        ans = f"{ans} {_sample_equation(concept)}"
    return [
        {"answer_type": "Words", "answer": ans,
         "answer_display": "Yes", "weightage": str(marks), "placeholder": "answer"},
    ]


def _descriptive_answers(concept: models.Concept, marks: float) -> tuple[list[dict], list[dict]]:
    body = f"Model answer covering {concept.concept_title}. See {_concept_reference_link(concept)}."
    if _is_math(concept):
        body = f"{body} Key relation: {_sample_equation(concept)}."
    answers = [
        {"answer_type": "Words", "answer_weightage": str(marks), "answer_content": body},
    ]
    # Keyword cells are NOT rich text — they hold raw KaTeX / plain text.
    sub = [
        {"text": f"i. Define {concept.concept_title}.", "marks": "2",
         "keywords": [{"answer_type": "Words", "weightage": "2",
                       "keyword": concept.concept_title}]},
        {"text": f"ii. Apply {concept.concept_title} to a worked example.",
         "marks": str(max(marks - 2, 1)),
         "keywords": [{"answer_type": "Words", "weightage": str(max(marks - 2, 1)),
                       "keyword": rf"\text{{{concept.concept_title}}} = f(x)"
                       if _is_math(concept) else "worked example"}]},
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
        # Future implementer: prepend kr.PROMPT_PREAMBLE to the system prompt so
        # the model emits rich-text columns in the bracket format the importer
        # expects (and keeps keyword cells in raw KaTeX).
        raise NotImplementedError(
            "Live question generation: wire bulk_upload_ultimate's GPT parsing/"
            "generation with OPENAI_API_KEY and the concept_details payload. "
            "Inject app.services.katex_rules.PROMPT_PREAMBLE as the system prompt."
        )

    marks = _default_marks(question_type)
    out: list[dict] = []
    details = (concept.concept_details or "").split("//")[0].strip()[:160]
    for i in range(count):
        idx = start_index + i
        question_text = (
            f"({difficulty} · {cognitive_skill}) "
            f"{category} on '{concept.concept_title}': {details}"
        )
        if _is_math(concept):
            question_text = f"{question_text} Express it as {_sample_equation(concept)}."
        record: dict = {
            "sheet_kind": question_type,
            "question_label": question_label(concept, idx),
            "question_category": category,
            "cognitive_skills": cognitive_skill,
            "question_source": "Aegis Concept Mapping",
            "level_of_difficulty": difficulty,
            "marks": marks,
            "question": question_text,
            "answer_explanation": (
                f"Assesses {concept.concept_title} ({cognitive_skill}). "
                f"Reference: {_concept_reference_link(concept)}."
            ),
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
            "Live extraction: wire bulk_upload_mathpix parsing with OPENAI_API_KEY. "
            "Inject app.services.katex_rules.PROMPT_PREAMBLE as the system prompt."
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

# Live concept-extraction prompts: ported from the vendored
# mmd_to_concepts_excel engine (subject-specific variants, 40-60 concept
# quota, quality rules) PLUS the team's review fixes, which the vendored
# prompt predates: inline worked examples (never bare "Example 19" refs),
# no '&' chains in names, distinct concept-name stems, Types must carry
# example prompts, syllabus-scoped length, no 'MMD' references.

_MATH_NAME_TEMPLATES = """\
   - Properties and Applications of <X>
   - Proof and Derivation of <rule/law>
   - Conditions for Applying <rule/law>
   - Representation of <X>
   - Conceptual Meaning of <X>
   - Methods of <procedure>
   - Laws and Applications of <X>
   - Converting Between <A> and <B>
   - Simplifying Using <rule/law>"""

_DESCRIPTIVE_NAME_TEMPLATES = """\
   - Structure and Function of <X>
   - Process of <X>
   - Types and Classification of <X>
   - Characteristics of <X>
   - Relationship between <A> and <B>
   - Causes and Effects of <X>
   - Importance and Significance of <X>
   - Comparison of <A> and <B>"""


def _concepts_system(subject: str) -> str:
    s = (subject or "the subject").strip() or "the subject"
    math_like = s.lower() in {"mathematics", "math", "physics"}
    templates = _MATH_NAME_TEMPLATES if math_like else _DESCRIPTIVE_NAME_TEMPLATES
    detail_line = (
        "definition, explanation, key properties, when/how to use, with worked "
        "examples and step-by-step reasoning INLINED in full"
        if math_like else
        "complete definition and explanation, key characteristics, processes or "
        "relationships, with concrete examples INLINED within the description"
    )
    return f"""\
You are a STRICT concept mapping engine for school {s} (board-level rigor).
Return ONLY a JSON object: {{"rows": [{{"topic": "", "concept": "", "concept_description": "", "keywords": ""}}, ...]}}.

OUTPUT CONTRACT (MUST FOLLOW EXACTLY):
- concept_description is ONE string with sections separated by " // " in this order:
  Description: <{detail_line}> // Types: <Type 01: Name Case 01: <concrete example prompt, e.g. 'Evaluate: ...', 'Prove: ...'> Case 02: ... Type 02: ...> // Misconception: <common student misconceptions> (omit section if none apply)
- Use " // " as the separator. Do NOT use newlines inside concept_description.

CONCEPT NAMING:
1) Names must be academic, specific and content-based. Prefer these templates:
{templates}
2) Sibling concepts must NOT repeat the same leading phrase; vary the stems.
3) NEVER chain names with '&'. Culmination rows are named
   "Culmination - <A>, <B> and <C>" (comma list with a final 'and').

TOPIC SEGREGATION:
- A topic groups 5-15 related concepts; follow the chapter's section flow.
- The LAST concept of every topic is exactly one culmination row synthesizing it.
- NEVER create a topic for exercises (Exercise 1.1, Ex 2.1...). Distribute
  exercise problems into the content topics they test, as extra Types/Cases.

TYPES:
- Classify EVERY question/numerical/problem variety found in the chapter
  (including exercise sections) under its concept.
- Zero-padded numbering (Type 01, Case 01). Every Case MUST carry a concrete
  example question/prompt.

SOURCE HYGIENE:
- NEVER reference source artifacts: no "Example 19", "Examples Type III",
  "Fig 2", "Table no. 1", "ex 1" - resolve each reference by inlining its
  actual worked content instead.
- NEVER use the words "MMD" or "MMDs"; say "chapter", "problem", "example".

QUALITY RULES:
- Produce 40-60 concepts (excluding culmination rows).
- No duplicates or near-duplicates: theory + exercise overlap -> output ONCE.
- No vague filler ("Introduction", "Misc", "Basics").
- Small, testable, taggable concepts; descriptions stay within syllabus scope
  (max ~90 words per section).
- keywords: 3-6 comma-separated lowercase terms.
"""


def _openai_json(system: str, user: str, max_tokens: int = 32000,
                 retries: int = 3) -> dict:
    """One JSON-mode chat call with retries; returns the parsed object."""
    import json
    import time
    from openai import OpenAI

    client = OpenAI()
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = client.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                max_completion_tokens=max_tokens,
            )
            return json.loads(resp.choices[0].message.content or "{}")
        except Exception as e:  # noqa: BLE001 — retry then surface
            last_err = e
            if attempt < retries:
                time.sleep(2)
    raise RuntimeError(f"OpenAI extraction failed after {retries} retries: {last_err!r}")


def _trim(text: str, max_chars: int = 220_000) -> str:
    if len(text) <= max_chars:
        return text
    return text[: int(max_chars * 0.7)] + "\n\n[...TRIMMED...]\n\n" + text[-int(max_chars * 0.3):]


def _is_culmination(title: str) -> bool:
    return (title or "").strip().lower().startswith("culmination")


def _ensure_culmination_per_topic(records: list[dict]) -> list[dict]:
    """Guarantee exactly one culmination row, last, for every topic.

    Mirrors the vendored engine's deterministic post-pass: keep the first
    culmination if several exist, synthesize one if the model omitted it.
    """
    topic_order: list[str] = []
    by_topic: dict[str, list[dict]] = {}
    for rec in records:
        topic = (rec.get("topic") or "General").strip() or "General"
        if topic not in by_topic:
            by_topic[topic] = []
            topic_order.append(topic)
        by_topic[topic].append(rec)

    out: list[dict] = []
    for topic in topic_order:
        rows = by_topic[topic]
        regular = [r for r in rows if not _is_culmination(r.get("concept_title", ""))]
        culms = [r for r in rows if _is_culmination(r.get("concept_title", ""))]
        out.extend(regular)
        if culms:
            out.append(culms[0])
        else:
            out.append({
                "topic": topic,
                "concept_title": f"Culmination - {topic}",
                "concept_details": (
                    f"Description: Consolidates the key ideas of '{topic}' into one "
                    "integrated understanding. // Types: Type 01: Mixed application "
                    "Case 01: Solve questions combining multiple concepts from this topic."
                ),
                "keywords": "",
            })
    return out


def concepts_from_mmd(mmd_text: str, *, subject: str = "",
                      live: bool | None = None) -> list[dict]:
    """Parse an MMD document into concept records (post-learning)."""
    use_live = config.use_live_generation() if live is None else live
    if use_live:
        data = _openai_json(
            _concepts_system(subject),
            "Extract 40-60 high-quality concepts from this chapter. Group them "
            "under topics (5-15+ concepts each, last row per topic is the "
            "culmination), following the chapter's section flow:\n\n"
            + _trim(mmd_text),
        )
        out = []
        for row in data.get("rows", []):
            title = (row.get("concept") or "").strip()
            if not title:
                continue
            out.append({
                "topic": (row.get("topic") or "Topic 01").strip(),
                "concept_title": title,
                "concept_details": (row.get("concept_description") or "").strip(),
                "keywords": (row.get("keywords") or "").strip(),
            })
        if not out:
            raise RuntimeError("live concept extraction returned no rows")
        return _ensure_culmination_per_topic(out)
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


_PRELEARNING_SYSTEM = """\
You derive PRE-LEARNING (prerequisite) concepts that a student must already
master before studying the given post-learning concepts.
Return ONLY a JSON object: {"rows": [{"topic": "", "concept": "", "concept_description": "", "keywords": ""}, ...]}.
- concept_description format: "Description: ... // Types: Type 01: Name Case 01: <example prompt> ... // Misconception: ..."
- Topic names should end with "(Pre-Learning)".
- One prerequisite concept per distinct foundational idea; merge overlaps.
- Same style rules as concept mapping: no '&' chains in names, no source
  references ("Example N", "Fig N", "MMD"), concise syllabus-scoped text,
  every Type's Cases carry a concrete example prompt.
"""


def pre_learning_from_concepts(concepts: list[models.Concept], *, live: bool | None = None) -> list[dict]:
    """Derive pre-learning concept records from existing post-learning concepts."""
    use_live = config.use_live_generation() if live is None else live
    if use_live:
        listing = "\n".join(
            f"- {c.concept_title}: {(c.concept_details or '')[:300]}" for c in concepts
        )
        data = _openai_json(
            _PRELEARNING_SYSTEM,
            "Derive prerequisite (pre-learning) concepts for these post-learning "
            "concepts:\n\n" + _trim(listing, 60_000),
        )
        out = []
        for row in data.get("rows", []):
            title = (row.get("concept") or "").strip()
            if not title:
                continue
            out.append({
                "topic": (row.get("topic") or "Foundations (Pre-Learning)").strip(),
                "concept_title": title,
                "concept_details": (row.get("concept_description") or "").strip(),
                "keywords": (row.get("keywords") or "").strip(),
            })
        if not out:
            raise RuntimeError("live pre-learning derivation returned no rows")
        return out
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
