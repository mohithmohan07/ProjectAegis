"""Normalize curriculum labels for consistent Title Case and spacing."""
from __future__ import annotations

import re
from pathlib import Path

# Words that stay lowercase in the middle of a title (unless first/last).
_SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "or", "the", "to", "via", "with", "vs", "vs.",
}

# Exact canonical spellings (lowercase key -> display form).
_CANONICAL = {
    "cbse": "CBSE",
    "icse": "ICSE",
    "kstate": "Karnataka",
    "karnataka": "Karnataka",
    "maharashtra": "Maharashtra",
    "msbshse": "Maharashtra",
    "maths": "Mathematics",
    "mathematics": "Mathematics",
    "math": "Mathematics",
    "english language": "English Language",
    "english": "English",
    "english grammar": "English Grammar",
    "english literature": "English Literature",
    "social science": "Social Science",
    "social studies": "Social Science",
    "sst": "Social Science",
    "evs": "Environmental Studies",
    "environmental studies": "Environmental Studies",
    "science": "Science",
    "physics": "Physics",
    "chemistry": "Chemistry",
    "biology": "Biology",
    "h&c": "History and Civics",
    "history and civics": "History and Civics",
    "history & civics": "History and Civics",
    "geography": "Geography",
    "civics": "Civics",
    "economics": "Economics",
    "hindi": "Hindi",
    "sanskrit": "Sanskrit",
    "computer science": "Computer Science",
    "information technology": "Information Technology",
}

# Acronyms that should stay fully uppercased when detected as a token.
_ACRONYMS = {"CBSE", "ICSE", "NCERT", "EVS", "IT", "AI", "DNA", "RNA", "pH"}


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _title_word(word: str, *, is_first: bool, is_last: bool) -> str:
    if not word:
        return word
    if word.upper() in _ACRONYMS:
        return word.upper()
    if re.fullmatch(r"[IVXLCDM]+", word, re.IGNORECASE):
        return word.upper()
    if re.fullmatch(r"\d+[A-Za-z]?", word):
        return word
    lower = word.lower()
    if not is_first and not is_last and lower in _SMALL_WORDS:
        return lower
    if "-" in word:
        parts = word.split("-")
        return "-".join(
            _title_word(p, is_first=(i == 0), is_last=(i == len(parts) - 1))
            for i, p in enumerate(parts)
        )
    if word.isupper() and len(word) <= 4:
        return word
    return word[0].upper() + word[1:].lower() if len(word) > 1 else word.upper()


def title_case_phrase(text: str) -> str:
    """Title-case a phrase, preserving hyphens and known acronyms."""
    text = _collapse_whitespace(text)
    if not text:
        return ""
    words = text.split(" ")
    last = len(words) - 1
    return " ".join(
        _title_word(w, is_first=(i == 0), is_last=(i == last))
        for i, w in enumerate(words)
    )


def normalize_board(raw: str, *, filename: str = "") -> str:
    """Normalize board names from cells or filenames."""
    probe = _collapse_whitespace(raw).lower()
    name = Path(filename).stem.lower() if filename else ""
    for key, canonical in (
        ("kstate", "Karnataka"), ("karnataka", "Karnataka"),
        ("maharashtra", "Maharashtra"), ("msbshse", "Maharashtra"),
        ("icse", "ICSE"), ("cbse", "CBSE"),
    ):
        if key in probe or key in name:
            return canonical
    if probe in _CANONICAL:
        return _CANONICAL[probe]
    return title_case_phrase(raw)


def normalize_subject(raw: str) -> str:
    """Normalize subject names to canonical Title Case."""
    text = _collapse_whitespace(raw)
    if not text:
        return ""
    key = text.lower()
    if key in _CANONICAL:
        return _CANONICAL[key]
    return title_case_phrase(text)


def normalize_unit(raw: str) -> str:
    """Normalize unit names."""
    text = _collapse_whitespace(raw)
    if not text:
        return ""
    # Strip board/grade tags embedded in unit labels, e.g. "(10_CBSE)" or "(06_KSTATE)".
    text = re.sub(r"\s*\(\d{2}_[A-Za-z]+\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[:;,\s]+$", "", text)
    return title_case_phrase(_collapse_whitespace(text))


def normalize_chapter(raw: str) -> str:
    """Normalize chapter names."""
    text = _collapse_whitespace(raw)
    if not text:
        return ""
    text = re.sub(r"[:;,\s]+$", "", text)
    # Remove leading enumeration like "1.", "01 -", "Chapter 3:"
    text = re.sub(r"^(?:chapter\s*)?\d+\s*[-.:)]\s*", "", text, flags=re.IGNORECASE)
    return title_case_phrase(_collapse_whitespace(text))


def normalize_grade(raw: str | int | float | None) -> str:
    """Normalize grade/class to two-digit string (01-12)."""
    if raw is None:
        return ""
    text = _collapse_whitespace(str(raw))
    if not text:
        return ""
    roman = roman_to_grade(text)
    if roman:
        return roman
    m = re.search(r"(\d{1,2})", text)
    if not m:
        return ""
    grade = int(m.group(1))
    if grade < 1 or grade > 12:
        return ""
    return f"{grade:02d}"


_ROMAN_GRADES = (
    "XII", "XI", "X", "IX", "VIII", "VII", "VI", "V", "IV", "III", "II", "I",
)


def roman_to_grade(text: str) -> str:
    """Convert a Roman numeral class label (e.g. VI, X) to a two-digit grade."""
    token = _collapse_whitespace(text).upper()
    if not token:
        return ""
    for roman in _ROMAN_GRADES:
        if token == roman:
            val = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
                   "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12}[roman]
            return f"{val:02d}"
    return ""
