"""Heuristic question→concept tagging.

Mirrors the spirit of `assessment_tagging/SmartWorkflow.gs` which uses GPT for
routing. In this MVP we score concepts by token overlap so the API works
without API keys; the live OpenAI path can be plugged in later.
"""
import re
from sqlalchemy.orm import Session

from .. import models, schemas

COGNITIVE_VERBS = {
    "Remembering": {"define", "list", "name", "recall", "state", "identify", "label"},
    "Understanding": {"explain", "describe", "summarize", "interpret", "classify", "compare"},
    "Applying": {"apply", "use", "compute", "calculate", "solve", "demonstrate"},
    "Analysing": {"analyze", "differentiate", "examine", "investigate", "categorize", "contrast"},
    "Evaluating": {"evaluate", "judge", "justify", "critique", "assess", "argue", "defend"},
    "Creating": {"design", "construct", "develop", "formulate", "propose", "compose", "plan"},
}

DIFFICULTY_HINTS = {
    "Less": {"define", "list", "state", "name", "what is"},
    "High": {"derive", "prove", "design", "evaluate", "critique", "justify"},
}

_TOKEN_RE = re.compile(r"[a-z][a-z0-9\-]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def infer_cognitive_skill(text: str) -> str:
    toks = _tokens(text)
    for skill, verbs in COGNITIVE_VERBS.items():
        if toks & verbs:
            return skill
    return "Understanding"


def infer_difficulty(text: str) -> str:
    low = text.lower()
    if any(h in low for h in DIFFICULTY_HINTS["High"]):
        return "High"
    if any(h in low for h in DIFFICULTY_HINTS["Less"]):
        return "Less"
    return "Moderate"


def _concept_tokens(c: models.Concept) -> set[str]:
    parts = " ".join([c.concept, c.parent_concept, c.topic, c.chapter_title, c.concept_description])
    return _tokens(parts)


def best_concept_match(db: Session, text: str) -> tuple[models.Concept | None, float]:
    concepts = db.query(models.Concept).all()
    if not concepts:
        return None, 0.0
    text_toks = _tokens(text)
    if not text_toks:
        return None, 0.0
    best: tuple[models.Concept | None, float] = (None, 0.0)
    for c in concepts:
        c_toks = _concept_tokens(c)
        if not c_toks:
            continue
        overlap = len(text_toks & c_toks)
        if overlap == 0:
            continue
        score = overlap / max(1, len(c_toks))
        if score > best[1]:
            best = (c, score)
    return best


def suggest(db: Session, text: str) -> schemas.TagSuggestion:
    concept, score = best_concept_match(db, text)
    path = ""
    if concept:
        path = " › ".join(p for p in [concept.subject, concept.chapter_title, concept.topic, concept.concept] if p)
    return schemas.TagSuggestion(
        concept_id=concept.id if concept else None,
        concept_path=path,
        cognitive_skills=infer_cognitive_skill(text),
        level_of_difficulty=infer_difficulty(text),
        confidence=round(min(score, 1.0), 3),
    )
