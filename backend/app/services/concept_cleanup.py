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

from .. import bulk_import as bi
from . import concept_refiner as cr
from . import katex_rules as kr

# Topics that must never appear as standalone teaching topics in output.
# Structural umbrellas only — never chapter- or subject-named content.
_FORBIDDEN_TOPIC_NAMES = {
    "overview", "basics", "basic concepts", "general",
    "summary", "misc", "miscellaneous",
}

# Pedagogy/instruction rows are task containers, not durable teaching concepts.
# Patterns are universal classroom-instruction labels (not chapter titles).
_PEDAGOGY_CONCEPT_RE = re.compile(
    r"\b(?:"
    r"pre-?\s*reading|informal letter|formal letter|letter writing|"
    r"reading in manageable parts|oral check|prediction and discussion|"
    r"comprehension drill|discussion questions|warm-?up activity|"
    r"think and discuss|classroom activity"
    r")\b",
    re.IGNORECASE,
)
_PEDAGOGY_TOPIC_RE = re.compile(
    r"\b(?:"
    r"pre-?\s*reading|informal letter|formal letter|letter writing|"
    r"reading in manageable parts|oral check|prediction and discussion|"
    r"classroom activity|think and discuss|warm-?up activity|"
    r"discussion questions|comprehension drill"
    r")\b",
    re.IGNORECASE,
)
_KNOWN_CONCEPT_ALIASES = {
    "bpt": "basic proportionality theorem",
    "basic proportionality theorem": "bpt",
    "cbpt": "converse basic proportionality theorem",
    "converse basic proportionality theorem": "cbpt",
}
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
# "no.", optionally "Type", followed by a number (incl. dotted forms like
# fig.11.1 / Exercise 1.5), number list, or roman numeral. Whitespace between
# the label and the number is optional — Mathpix OCR often emits "fig.11.1".
_REF_NUM = r"(?:[IVXLCDM]+|\d+(?:\.\d+)*(?:\s*[,&]\s*\d+(?:\.\d+)*)*)"
_REF_CORE = (
    r"(?:examples?|ex|fig(?:ure)?s?|tables?)\b\.?\s*(?:no\.?\s*)?"
    r"(?:type\s+)?" + _REF_NUM + r"\b"
)
# Same reference token WITHOUT figure/table: when the actual image is embedded
# as a URL, figure/table references are meaningful and must be preserved.
_REF_CORE_NO_FIG = (
    r"(?:examples?|ex)\b\.?\s*(?:no\.?\s*)?"
    r"(?:type\s+)?" + _REF_NUM + r"\b"
)
# Parenthetical reference, e.g. "(Example 19)", "(Examples Type III)", "(see Fig 2)".
_PAREN_REF_RE = re.compile(
    r"\(\s*(?:see\s+)?(?:" + _REF_CORE + r")\s*\)", re.IGNORECASE,
)
_PAREN_REF_NO_FIG_RE = re.compile(
    r"\(\s*(?:see\s+)?(?:" + _REF_CORE_NO_FIG + r")\s*\)", re.IGNORECASE,
)
# Bare inline reference, optionally led by a connector ("and"/"or"/",") and/or a
# cue word ("see"/"refer"). Consuming the leading connector keeps multi-reference
# clauses ("Table 1 and Figure 2 or Example 3") from leaving stranded "and/or".
_INLINE_REF_RE = re.compile(
    r"(?:[,]\s*|\b(?:and|or)\s+)?(?:\b(?:see|refer(?:\s+to)?)\s+)?" + _REF_CORE,
    re.IGNORECASE,
)
_INLINE_REF_NO_FIG_RE = re.compile(
    r"(?:[,]\s*|\b(?:and|or)\s+)?(?:\b(?:see|refer(?:\s+to)?)\s+)?" + _REF_CORE_NO_FIG,
    re.IGNORECASE,
)
# Markdown image or bare URL — presence means real source visuals are embedded.
_IMAGE_URL_RE = re.compile(
    r"!\[[^\]]*\]\(https?://[^)]+\)|"
    r'\[img\s+src="https?://[^"]+"\s+alt="[^"]+"[^\]]*\]|'
    r"https?://\S+",
    re.IGNORECASE,
)


def filter_review_violations(
    records: list[dict], *, subject: str = "", board: str = "",
    chapter_title: str = "",
) -> list[dict]:
    """Drop or reassign rows that QA flagged across subject samples."""
    if not records:
        return records

    real_topics: list[str] = []
    for rec in records:
        topic = (rec.get("topic") or "").strip()
        key = bi.normalize_question_text(topic)
        if (
            key
            and key not in _FORBIDDEN_TOPIC_NAMES
            and not _PEDAGOGY_TOPIC_RE.search(topic)
            and topic not in real_topics
        ):
            real_topics.append(topic)
    fallback_topic = (
        real_topics[0]
        if real_topics
        else (to_title_case(chapter_title.strip()) or records[0].get("topic") or "General")
    )

    out: list[dict] = []
    dropped = 0
    for rec in records:
        title = (rec.get("concept_title") or "").strip()
        topic = (rec.get("topic") or "").strip()
        topic_key = bi.normalize_question_text(topic)

        if _PEDAGOGY_CONCEPT_RE.search(title):
            dropped += 1
            continue
        if _PEDAGOGY_TOPIC_RE.search(topic):
            rec = dict(rec)
            rec["topic"] = fallback_topic
            out.append(rec)
            continue
        # Overview / Summary / Basics topics are omitted entirely — never
        # pushed into a neighboring topic (that caused repeated preview/recap).
        # Classroom discussion cases / activity blocks are classified by the
        # GPT Activity/Info Hub pass, not by chapter-named regex filters.
        if topic_key in _FORBIDDEN_TOPIC_NAMES:
            dropped += 1
            continue
        out.append(rec)

    if dropped:
        from . import progress as _progress
        _progress.log(
            f"Dropped {dropped} pedagogy / filler concept row(s).",
            level="warning",
        )
    return out


def _title_similarity_keys(title: str) -> set[str]:
    norm = bi.normalize_question_text(title)
    words = [w for w in norm.split() if w not in {"the", "a", "an", "and", "of"}]
    keys = {norm, " ".join(words)}
    if len(words) >= 2 and all(w.isalpha() for w in words):
        keys.add("".join(w[0] for w in words))
    tokens = set(norm.split())
    for alias, expansion in _KNOWN_CONCEPT_ALIASES.items():
        if alias in keys or expansion in keys or alias in tokens:
            keys.add(alias)
            keys.add(expansion)
    return {k for k in keys if k}


def titles_look_similar(a: str, b: str) -> bool:
    """True when two concept titles restate the same idea (BPT vs. its echo)."""
    ka, kb = _title_similarity_keys(a), _title_similarity_keys(b)
    if ka & kb:
        return True
    na, nb = bi.normalize_question_text(a), bi.normalize_question_text(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        if ("converse" in na.split()) != ("converse" in nb.split()):
            return False
        return True
    return False


def find_similar_title_groups(records: list[dict]) -> list[list[int]]:
    """Groups of row indexes whose titles restate the same concept.

    Detector only — deciding WHICH content to keep/merge is a quality call
    that belongs to the GPT merge pass; this never modifies records.
    """
    groups: list[list[int]] = []
    for i, rec in enumerate(records):
        title = (rec.get("concept_title") or "").strip()
        if not title or cr.is_culmination(title):
            continue
        for group in groups:
            if titles_look_similar(
                    title, (records[group[0]].get("concept_title") or "")):
                group.append(i)
                break
        else:
            groups.append([i])
    return [g for g in groups if len(g) > 1]


def dedupe_similar_titles_chapter_wide(records: list[dict]) -> list[dict]:
    """Drop near-duplicate concept titles (e.g. BPT restated under two topics).

    Deterministic last resort — the live pipeline first asks GPT to MERGE the
    duplicate rows' content (``_merge_similar_concepts_via_api``); this drop
    only runs in dry mode or when that pass failed.
    """
    def required(rec: dict) -> bool:
        return bool(re.search(
            r"\bMETHOD-[A-F0-9]{10}\b",
            str(rec.get("source_evidence") or ""),
            re.IGNORECASE,
        ))

    kept: list[str] = []
    out: list[dict] = []
    dropped = 0
    for rec in records:
        title = (rec.get("concept_title") or "").strip()
        if title and not cr.is_culmination(title):
            similar_index = next(
                (i for i, prev in enumerate(kept)
                 if titles_look_similar(title, prev)),
                None,
            )
            if similar_index is not None:
                if required(rec) and required(out[similar_index]):
                    kept.append(title)
                    out.append(rec)
                    continue
                previous = out[similar_index]
                survivor = rec if required(rec) else previous
                qids = list(survivor.get("_activity_hub_qids") or [])
                for source in (previous, rec):
                    for qid in source.get("_activity_hub_qids") or []:
                        if qid not in qids:
                            qids.append(qid)
                if qids != list(survivor.get("_activity_hub_qids") or []):
                    survivor = dict(survivor)
                    survivor["_activity_hub_qids"] = qids
                if required(rec):
                    kept[similar_index] = title
                out[similar_index] = survivor
                dropped += 1
                continue
            kept.append(title)
        out.append(rec)
    if dropped:
        from . import progress as _progress
        _progress.log(
            f"Dropped {dropped} near-duplicate concept-title row(s) chapter-wide.",
            level="warning",
        )
    return out


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


# Control characters (except \t, \n, \r) are illegal in Excel worksheets and
# meaningless in concept text; models occasionally emit one (e.g. a degree
# sign mangled into \x04 by OCR round-trips). Strip them at the source.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_control_chars(text: str) -> str:
    return _CONTROL_CHARS_RE.sub("", text) if text else text


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
    # Neutralization can stack ("as shown in in the chapter", "as in in the chapter").
    text = re.sub(
        r"\b(?:as\s+shown\s+)?(?:in|on)\s+in\s+the\s+chapter\b",
        "in the chapter",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bas\s+in\s+in\s+the\s+chapter\b",
        "in the chapter",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def strip_dangling_references(text: str) -> str:
    """Remove bare source-artifact references; keep real worded content.

    When the text embeds an actual image URL, figure/table references stay
    (they point at the shipped image, e.g. "(Refer fig. 11.1) ![](https://…)").
    """
    if not text:
        return text
    has_image = bool(_IMAGE_URL_RE.search(text))
    paren_re = _PAREN_REF_NO_FIG_RE if has_image else _PAREN_REF_RE
    inline_re = _INLINE_REF_NO_FIG_RE if has_image else _INLINE_REF_RE
    out = paren_re.sub("", text)

    def _inline_sub(m: re.Match) -> str:
        # Drop the reference and an immediately-trailing dangling connector.
        return ""

    out = inline_re.sub(_inline_sub, out)
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


# Validator-aligned neutralization of source references. The concept validator
# hard-fails rows containing "Exercise 1.2" / "Example 5" / "Fig 3" / "page 12"
# style artifacts anywhere (including Types/Case prompts, which are mined from
# real source questions and often carry their labels). LLM repair passes keep
# recreating these, so they are rewritten deterministically into neutral
# academic wording that preserves the task content.
# Keep these aligned with concept_validator._SOURCE_ARTIFACT_RE. Whitespace and
# the dot after Fig/Ex/page are optional so OCR forms like fig.11.1 / p14 /
# page14 / Example11 all neutralize.
_ARTIFACT_NEUTRALIZATIONS = [
    (re.compile(r"\b(?:exercises?|ex)\.?\s*\d+(?:\.\d+)*\b", re.IGNORECASE),
     "the exercises"),
    (re.compile(r"\bexamples?\.?\s*\d+(?:\.\d+)*\b", re.IGNORECASE),
     "a worked example"),
    (re.compile(r"\b(?:on\s+)?pages?\.?\s*(?:no\.?\s*)?\d+\b", re.IGNORECASE),
     "in the chapter"),
    # Bare "p14" / "p.14" / "p 14" page pointers (not "power"/"amp" — needs digit).
    (re.compile(r"\bp\.?\s*\d+\b", re.IGNORECASE),
     "in the chapter"),
]
# Figure/table references are only neutralized when the row ships NO image —
# with an embedded Mathpix URL they point at real, visible content.
# Optional whitespace covers OCR forms like "fig.11.1" and "Fig.11.2".
_FIG_TABLE_NEUTRALIZATIONS = [
    (re.compile(r"\bfig(?:ure)?s?\.?\s*\d+(?:\.\d+)*\b", re.IGNORECASE),
     "the figure"),
    (re.compile(r"\btables?\.?\s*\d+(?:\.\d+)*\b", re.IGNORECASE),
     "the given table"),
]


def neutralize_source_artifacts(text: str) -> str:
    """Rewrite bare source references into neutral wording (content kept)."""
    if not text:
        return text
    for pat, repl in _ARTIFACT_NEUTRALIZATIONS:
        text = pat.sub(repl, text)
    if not _IMAGE_URL_RE.search(text):
        for pat, repl in _FIG_TABLE_NEUTRALIZATIONS:
            text = pat.sub(repl, text)
    return _tidy(text)


# Last-resort patterns that mirror concept_validator._SOURCE_ARTIFACT_RE so a
# residual match can never survive into final validation. Prefer the named
# rewrites above; these only fire when a validator-shaped token remains.
_VALIDATOR_ALIGNED_SCRUBS = [
    (re.compile(r"\bMMDs?\b", re.IGNORECASE), "chapter"),
    (re.compile(
        r"\bExamples?\.?\s*\d+(?:\.\d+)*\b", re.IGNORECASE), "a worked example"),
    (re.compile(
        r"\b(?:Exercises?|Ex)\.?\s*\d+(?:\.\d+)*\b", re.IGNORECASE),
     "the exercises"),
    (re.compile(
        r"\b(?:on\s+)?pages?\.?\s*(?:no\.?\s*)?\d+\b", re.IGNORECASE),
     "in the chapter"),
    (re.compile(r"\bp\.?\s*\d+\b", re.IGNORECASE), "in the chapter"),
]
_VALIDATOR_ALIGNED_FIG_SCRUBS = [
    (re.compile(
        r"\bFig(?:ure)?s?\.?\s*\d+(?:\.\d+)*\b", re.IGNORECASE), "the figure"),
    (re.compile(
        r"\bTables?\.?\s*\d+(?:\.\d+)*\b", re.IGNORECASE), "the given table"),
]


def scrub_validator_artifacts(text: str) -> str:
    """Force-clear any token the concept validator treats as source_artifact.

    Used as a final deposit guarantee after named neutralization. Figure/table
    refs are kept when a Mathpix/CDN image URL is already embedded.
    """
    if not text:
        return text
    text = replace_mmd_references(text)
    for pat, repl in _VALIDATOR_ALIGNED_SCRUBS:
        text = pat.sub(repl, text)
    if not _IMAGE_URL_RE.search(text):
        for pat, repl in _VALIDATOR_ALIGNED_FIG_SCRUBS:
            text = pat.sub(repl, text)
    return _tidy(text)


# Canonical separator between Description / Types / Misconception sections.
_SECTION_SEP = " // "


_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(https?://[^)]+\)", re.IGNORECASE)
_BRACKET_IMAGE_RE = re.compile(
    r'\[img\s+src="https?://[^"]+"(?:\s+alt="[^"]*")?[^\]]*\]',
    re.IGNORECASE,
)
_TEXTBOOK_SECTION_REF_RE = re.compile(
    r"(?:\bsections?\s+|§\s*)\d+(?:\.\d+)+\b",
    re.IGNORECASE,
)


def _strip_images_from_prose(text: str) -> str:
    """Remove shipped images from Description/Misconception prose.

    Image URLs are valid in Types Examples and Activity/Info Hub entries (with
    their figure reference); they are not acceptable in Description text.
    """
    if not text:
        return text
    return _tidy(_BRACKET_IMAGE_RE.sub("", _MARKDOWN_IMAGE_RE.sub("", text)))


def _clean_details(details: str, *, neutralize: bool = True) -> str:
    """Sanitize a concept_details string section-by-section.

    The Types section is preserved verbatim (only MMD wording is rewritten) so
    ``Type NN:`` / ``Case NN:`` labels and example prompts are never damaged by
    reference-stripping. Other sections get full dangling-reference removal. The
    ``" // "`` separators are kept intact so downstream section parsing works.

    ``neutralize=False`` keeps source references ("Exercise 1.2", "Example 5")
    intact so the LLM repair pass can substitute the actual condensed problem
    content from the source; the preferred outcome is real numericals in the
    text, with neutral wording only as the post-repair last resort.
    """
    parts = details.split(_SECTION_SEP)
    out: list[str] = []
    for part in parts:
        label = part.split(":", 1)[0].strip().lower() if ":" in part else ""
        is_types = label.startswith("type")
        is_hub = (
            label.replace(" ", "").replace("/", "").startswith("activityinfohub")
            or label.replace(" ", "").startswith("activityhub")
        )
        if is_types or is_hub or not neutralize:
            # Types/Hub keep structure; and when the caller wants references
            # preserved for content inlining, prose keeps them too.
            cleaned = replace_mmd_references(part)
        else:
            cleaned = replace_mmd_references(strip_dangling_references(part))
        if label.startswith("description") or label.startswith("misconception"):
            cleaned = _TEXTBOOK_SECTION_REF_RE.sub("the chapter", cleaned)
        # Mathpix URLs are Types/Hub-only; strip them from Description and
        # Misconception even during reference-preserving pre-repair cleanup.
        if not is_types and not is_hub:
            cleaned = _strip_images_from_prose(cleaned)
        out.append(neutralize_source_artifacts(cleaned) if neutralize else cleaned)
    return _SECTION_SEP.join(out)


def _neutralize_name_artifacts(name: str) -> str:
    """Drop source references from a name; keep the wording around them.

    Unlike prose, a name reading "... in a worked example" is worse than one
    with the reference simply removed, so names strip rather than reword.
    """
    if not name:
        return name
    out = strip_dangling_references(name)
    out = re.sub(
        r"\b(?:on\s+)?pages?\.?\s*(?:no\.?\s*)?\d+\b", " ",
        out, flags=re.IGNORECASE)
    out = re.sub(r"\bp\.?\s*\d+\b", " ", out, flags=re.IGNORECASE)
    out = re.sub(
        r"\b(?:exercises?|ex)\.?\s*\d+(?:\.\d+)*\b", " ", out, flags=re.IGNORECASE)
    out = re.sub(
        r"\b(?:examples?|fig(?:ure)?s?|tables?)\.?\s*\d+(?:\.\d+)*\b", " ",
        out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip(" -:.,") or name


def clean_concept_record(rec: dict, *, neutralize_artifacts: bool = True) -> dict:
    """Return ``rec`` with its name + description normalized (mutates in place).

    ``neutralize_artifacts=False`` leaves source references ("Exercise 1.2",
    "Example 5") in place so the LLM repair pass can replace them with the
    actual condensed problem content; pass ``True`` (default) as the final
    deterministic guarantee that no reference survives to strict validation.
    """
    for field in ("topic", "parent_concept", "concept_title", "concept_details"):
        if rec.get(field):
            rec[field] = strip_control_chars(rec[field])
    if rec.get("topic"):
        topic = rec["topic"].strip()
        rec["topic"] = to_title_case(
            _neutralize_name_artifacts(topic) if neutralize_artifacts else topic)
    if rec.get("parent_concept"):
        parent = clean_concept_name(rec["parent_concept"].strip())
        if neutralize_artifacts:
            parent = _neutralize_name_artifacts(parent)
        rec["parent_concept"] = to_title_case(replace_mmd_references(parent))
    if rec.get("concept_title"):
        title = clean_concept_name(rec["concept_title"])
        if neutralize_artifacts:
            title = _neutralize_name_artifacts(title)
        rec["concept_title"] = to_title_case(replace_mmd_references(title))
    if rec.get("concept_details"):
        rec["concept_details"] = kr.canonicalize_rich_text(
            rec["concept_details"])
        rec["concept_details"] = _clean_details(
            rec["concept_details"], neutralize=neutralize_artifacts)
    if neutralize_artifacts:
        # Absolute last resort: scrub any validator-shaped token that named
        # neutralization missed (OCR forms, MMD leftovers, page14, etc.).
        for field in ("topic", "parent_concept", "concept_title", "concept_details"):
            if rec.get(field):
                rec[field] = scrub_validator_artifacts(rec[field])
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
