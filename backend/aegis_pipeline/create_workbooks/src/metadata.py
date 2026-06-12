"""Infer subject / grade / chapter number / title from NCERT filename."""
from __future__ import annotations

import re
from pathlib import Path

SUBJECT_DISPLAY = {
    "science": "Science",
    "mathematics": "Mathematics",
    "socialscience": "Social Science",
    "social_science": "Social Science",
    "english": "English",
}


_LOWERCASE_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with"}


def slug_to_title(slug: str) -> str:
    raw = slug.replace("_", " ").strip()
    raw = re.sub(r"\bIndias\b", "India's", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\bI M\b", "I'm", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\bHow I Thought\b", "How I Taught", raw, flags=re.IGNORECASE)
    words = raw.split()
    out: list[str] = []
    for i, word in enumerate(words):
        lower = word.lower()
        if i != 0 and lower in _LOWERCASE_WORDS:
            out.append(lower)
        else:
            out.append(word.capitalize() if not word.isupper() or len(word) <= 3 else word.title())
    return " ".join(out)


def infer_metadata(pdf_path: str | Path) -> dict[str, str]:
    path = Path(pdf_path)
    pattern = re.compile(
        r"Class[_\s]?(\d{2}).*?G(\d{2})_(Science|Mathematics|SocialScience|English)"
        r".*?(?:CH|UN)(\d{2})_(.+)\.pdf",
        re.IGNORECASE,
    )
    match = pattern.search(str(path))
    if not match:
        return {}
    grade, _, subject_key, chapter_no, slug = match.groups()
    return {
        "grade": f"Grade {int(grade)}",
        "subject": SUBJECT_DISPLAY.get(subject_key.lower(), subject_key),
        "chapter_number": chapter_no,
        "chapter_title": slug_to_title(slug),
        "discipline": infer_discipline(slug_to_title(slug), SUBJECT_DISPLAY.get(subject_key.lower(), subject_key)),
    }


_BIOLOGY_HINTS = (
    "cell", "life", "tissue", "organ", "plant", "animal", "nutrition",
    "respiration", "reproduction", "heredity", "evolution", "microbe",
    "microorganism", "ecosystem", "diversity", "body", "health", "disease",
    "photosynthesis", "digestion", "excretion", "transport", "hormone",
)
_CHEMISTRY_HINTS = (
    "matter", "atom", "molecule", "acid", "base", "metal", "carbon",
    "chemical", "reaction", "periodic", "mixture", "solution", "element",
    "compound", "mole", "bond",
)
_PHYSICS_HINTS = (
    "motion", "force", "energy", "light", "sound", "wave", "electric",
    "magnet", "gravitation", "work", "power", "pressure", "float",
    "current", "voltage", "reflection", "refraction",
)


def infer_discipline(chapter_title: str, subject: str = "") -> str:
    """Guess Science sub-discipline from chapter title (for Biology-only rules)."""
    if subject != "Science":
        return ""
    t = chapter_title.lower()
    scores = {
        "Biology": sum(1 for k in _BIOLOGY_HINTS if k in t),
        "Chemistry": sum(1 for k in _CHEMISTRY_HINTS if k in t),
        "Physics": sum(1 for k in _PHYSICS_HINTS if k in t),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else ""
