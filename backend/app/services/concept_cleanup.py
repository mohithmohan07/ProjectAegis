"""Deterministic concept-output normalization.

These cleaners run on every generated concept (dry or live) so the Bulk Import
rows are import-clean regardless of which extractor produced them. They address
team-reported concept-mapping defects that are safe to fix without an LLM:

  * Multiple ``&`` in concept names  -> comma list with a final "and"
    ("Culmination - A & B & C" -> "Culmination - A, B and C").
    A single ``&`` (e.g. "History & Civics") is left alone.
  * Dangling source references in descriptions ("(Example 19)", "Examples
    Type III", "Figure 1,2", "Table no. 1", "ex 1") -> removed. Actual worded
    content like "worked example: ..." is preserved.

Two non-destructive detectors are also provided for defects whose real fix is
prompt-side (so callers can flag rather than silently rewrite):

  * ``detect_repeated_leading_phrase`` - sibling concepts sharing a lead phrase.
  * ``description_length_report``      - sections longer than a word budget.
"""
from __future__ import annotations

import re
import string

# Connector words kept lowercase in Title Case (unless first/last word).
_TITLE_SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "onto", "or", "over", "per", "the", "to", "up", "via",
    "vs", "with",
}


def _capitalize_first_letter(word: str) -> str:
    """Upper-case the first alphabetic char, lower-case the rest (keeps punctuation)."""
    for i, ch in enumerate(word):
        if ch.isalpha():
            return word[:i] + ch.upper() + word[i + 1:].lower()
    return word


def to_title_case(text: str) -> str:
    """Convert a title to Title Case, preserving acronyms/units and connectors.

    - First and last words are always capitalized.
    - Small connector words (of, and, the, ...) stay lowercase in the middle.
    - Tokens with internal capitals (e.g. ``ICSE``, ``pH``, ``NaCl``) or digits
      (``H2O``, ``2x``) are left untouched.
    """
    if not text or not text.strip():
        return text
    words = text.split(" ")
    last = len(words) - 1
    out: list[str] = []
    for i, w in enumerate(words):
        if not w:
            out.append(w)
            continue
        letters = [c for c in w if c.isalpha()]
        # Preserve acronyms / camel-case / anything with digits.
        if any(c.isdigit() for c in w) or (letters and any(c.isupper() for c in letters[1:])):
            out.append(w)
            continue
        bare = w.strip(string.punctuation).lower()
        if 0 < i < last and bare in _TITLE_SMALL_WORDS:
            out.append(w.lower())
            continue
        out.append(_capitalize_first_letter(w))
    return " ".join(out)

# A reference token: Example(s) / Ex / Figure(s) / Fig / Table, optionally
# "no.", optionally "Type", followed by a number, number list, or roman numeral.
_REF_CORE = (
    r"(?:examples?|ex|fig(?:ure)?s?|tables?)\b\.?\s*(?:no\.?\s*)?"
    r"(?:type\s+)?(?:[IVXLCDM]+|\d+(?:\s*[,&]\s*\d+)*)\b"
)
# Parenthetical reference, e.g. "(Example 19)", "(Examples Type III)", "(see Fig 2)".
_PAREN_REF_RE = re.compile(
    r"\(\s*(?:see\s+)?(?:" + _REF_CORE + r")\s*\)", re.IGNORECASE,
)
# Bare inline reference, optionally led by a connector ("and"/"or"/",") and/or a
# cue word ("see"/"refer"). Consuming the leading connector keeps multi-reference
# clauses ("Table 1 and Figure 2 or Example 3") from leaving stranded "and/or".
_INLINE_REF_RE = re.compile(
    r"(?:[,]\s*|\b(?:and|or)\s+)?(?:\b(?:see|refer(?:\s+to)?)\s+)?" + _REF_CORE,
    re.IGNORECASE,
)


def clean_concept_name(name: str) -> str:
    """Collapse 2+ space-delimited ``&`` separators into a comma list + 'and'."""
    if not name:
        return name
    parts = re.split(r"\s+&\s+", name.strip())
    if len(parts) < 3:  # 0 or 1 "&": leave proper nouns like "History & Civics".
        return name.strip()
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < 3:
        return name.strip()
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _tidy(text: str) -> str:
    """Repair whitespace/punctuation artifacts left after deletions."""
    text = re.sub(r"\(\s*\)", "", text)            # empty parens
    text = re.sub(r"[ \t]{2,}", " ", text)          # collapsed spaces
    text = re.sub(r"\s+([.;,:])", r"\1", text)      # space before punctuation
    text = re.sub(r"([(])\s+", r"\1", text)         # space after "("
    text = re.sub(r"\s+\)", ")", text)              # space before ")"
    text = re.sub(r"(?:\s*,){2,}", ",", text)       # doubled commas
    text = re.sub(r"\bsee\s*([.;,])", r"\1", text, flags=re.IGNORECASE)  # orphan "see"
    text = re.sub(r"\b(?:or|and)\s*([.;])", r"\1", text, flags=re.IGNORECASE)
    return text.strip()


def strip_dangling_references(text: str) -> str:
    """Remove bare source-artifact references; keep real worded content."""
    if not text:
        return text
    out = _PAREN_REF_RE.sub("", text)

    def _inline_sub(m: re.Match) -> str:
        # Drop the reference and an immediately-trailing dangling connector.
        return ""

    out = _INLINE_REF_RE.sub(_inline_sub, out)
    # Clean any connector the removal still stranded before punctuation / line end.
    out = re.sub(r"\s*\b(?:or|and)\s+(?=[.;,)]|$)", "", out, flags=re.IGNORECASE)
    # Drop an orphan leading connector left at the very start.
    out = re.sub(r"^\s*(?:,|\b(?:and|or)\b)\s+", "", out, flags=re.IGNORECASE)
    return _tidy(out)


# "MMD" is a source-format artifact ("Reference MMD in CD"); concept rows must
# speak in normal academic language. Mirrors the pipeline's sanitize_mmd_references.
_MMD_REPLACEMENTS = [
    (re.compile(r"\bMMD\s+problems\b", re.IGNORECASE), "problems"),
    (re.compile(r"\bMMD\s+problem\b", re.IGNORECASE), "problem"),
    (re.compile(r"\b(in|from)\s+the\s+MMDs?\b", re.IGNORECASE), r"\1 the chapter"),
    (re.compile(r"\bthe\s+MMDs?\b", re.IGNORECASE), "the chapter"),
    (re.compile(r"\bMMDs?\b", re.IGNORECASE), "chapter"),
]


def replace_mmd_references(text: str) -> str:
    """Rewrite 'MMD' source references into natural chapter language."""
    if not text:
        return text
    for pat, repl in _MMD_REPLACEMENTS:
        text = pat.sub(repl, text)
    return text


# Canonical separator between Description / Types / Misconception sections.
_SECTION_SEP = " // "


def _clean_details(details: str) -> str:
    """Sanitize a concept_details string section-by-section.

    The Types section is preserved verbatim (only MMD wording is rewritten) so
    ``Type NN:`` / ``Case NN:`` labels and example prompts are never damaged by
    reference-stripping. Other sections get full dangling-reference removal. The
    ``" // "`` separators are kept intact so downstream section parsing works.
    """
    parts = details.split(_SECTION_SEP)
    out: list[str] = []
    for part in parts:
        label = part.split(":", 1)[0].strip().lower() if ":" in part else ""
        if label.startswith("type"):
            out.append(replace_mmd_references(part))
        else:
            out.append(replace_mmd_references(strip_dangling_references(part)))
    return _SECTION_SEP.join(out)


def clean_concept_record(rec: dict) -> dict:
    """Return ``rec`` with its name + description normalized (mutates in place)."""
    if rec.get("topic"):
        rec["topic"] = to_title_case(rec["topic"].strip())
    if rec.get("concept_title"):
        rec["concept_title"] = to_title_case(replace_mmd_references(
            clean_concept_name(rec["concept_title"])))
    if rec.get("concept_details"):
        rec["concept_details"] = _clean_details(rec["concept_details"])
    return rec


# --------------------------------------------------------------------------- #
# Non-destructive detectors (real fix is prompt-side)
# --------------------------------------------------------------------------- #

_STOPWORD_TAIL = {"of", "the", "a", "an", "and", "for", "to", "in", "on"}


def detect_repeated_leading_phrase(
    names: list[str], *, min_words: int = 2, min_group: int = 2,
) -> dict | None:
    """Report the longest leading word-phrase shared by >= ``min_group`` names."""
    tokenized = [re.findall(r"\w+", (n or "").lower()) for n in names]
    tokenized = [t for t in tokenized if t]
    if len(tokenized) < min_group:
        return None

    best: list[str] | None = None
    longest = max(len(t) for t in tokenized)
    # Search long -> short so the fullest shared phrase wins; a short unrelated
    # name simply doesn't contribute to longer-prefix counts.
    for length in range(longest, min_words - 1, -1):
        prefixes: dict[tuple, int] = {}
        for toks in tokenized:
            if len(toks) >= length:
                prefixes[tuple(toks[:length])] = prefixes.get(tuple(toks[:length]), 0) + 1
        for prefix, count in prefixes.items():
            if count >= min_group and list(prefix)[-1] not in _STOPWORD_TAIL:
                best = list(prefix)
                break
        if best:
            break
    if not best:
        return None
    phrase_lc = best
    affected = [
        n for n, toks in zip(names, tokenized)
        if toks[:len(phrase_lc)] == phrase_lc
    ]
    return {"phrase": " ".join(phrase_lc), "count": len(affected), "names": affected}


def description_length_report(details: str, *, max_words_per_section: int = 90) -> dict:
    """Flag ``Description/Types/Misconception`` sections over a word budget."""
    sections = re.split(r"\s*//\s*|\n", details or "")
    over = []
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        label = sec.split(":", 1)[0].strip() if ":" in sec[:20] else sec[:20]
        words = len(re.findall(r"\w+", sec))
        if words > max_words_per_section:
            over.append({"section": label, "words": words})
    return {"over_budget": over, "max_words_per_section": max_words_per_section}
