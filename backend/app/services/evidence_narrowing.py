"""Terminal disposition: narrow a concept to what its evidence supports.

Every earlier rung of the grounding ladder tries to make a concept correct:
Phase 3.1 grounds it, Phase 3.2 moves/refines/splits it, Phase 3.8 spends a
bounded convergence budget and then one final atomisation pass.  This module
is the rung *after* all of those, and it exists because of one product rule:

    A run must always produce the output workbook.  It may not stop, and it
    may not delete a concept in order to finish.

The disposition therefore does the only thing that satisfies both halves:
it **modifies the concept to match its evidence**.  Clauses the canonical
source blocks actually support are kept; clauses they do not support are
removed and named in the audit.  If nothing in the written claim survives,
the concept is restated from the source text itself, verbatim, so that what
ships is supported by construction.

Three properties make this safe to run unattended:

* **Deterministic.**  No provider call, no sampling, no ordering luck.  The
  same records and the same canonical source always yield the same output,
  so a Resume reproduces the disposition byte for byte rather than paying
  for a new one.
* **Conservative.**  A clause is kept only when every content token in it
  appears in the cited evidence.  The test errs towards dropping a clause,
  never towards keeping an unsupported one.
* **Lossless in count.**  Rows are rewritten, never removed.  The caller
  receives exactly the concepts it passed in.

What this module does *not* do is decide placement.  Ownership is settled by
``placement_policy``; narrowing only ever changes what a concept claims, not
which topic teaches it.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from . import concept_refiner as cr
from . import grounding_certificate


DISPOSITION_VERSION = "aegis-evidence-narrowing-1"

#: Written onto every row this module rewrites.
NARROWED_FLAG_FIELD = "_evidence_narrowed"
NARROWED_KIND_FIELD = "_evidence_narrowed_kind"
NARROWED_DROPPED_FIELD = "_evidence_narrowed_dropped_clauses"
NARROWED_EVIDENCE_FIELD = "_evidence_narrowed_block_ids"
NARROWED_NOTE_FIELD = "_evidence_narrowed_note"
NARROWED_ORIGINAL_FIELD = "_evidence_narrowed_original_claim"

#: Grounding contract recorded for a claim rebuilt out of source text.  It is
#: deliberately distinct from the model-verified contracts so an auditor can
#: separate "a provider asserted this is grounded" from "deterministic code
#: proved every token came from the cited blocks".
NARROWED_CONTRACT = "deterministic-evidence-narrowed-source-claim"

_DESCRIPTION_LABEL = "Description"

#: Longest verbatim restatement kept for a concept with no surviving clause.
_MAX_RESTATEMENT_CHARS = 600

_SPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Sentence break: a terminator followed by whitespace, not preceded by a digit
# (so "5.4" and "1.5 m" stay intact) and not part of a common abbreviation.
_SENTENCE_BREAK_RE = re.compile(r"(?<![0-9])(?<!\be\.g)(?<!\bi\.e)([.!?;])\s+")

# Inline and display maths are opaque to clause splitting; a "." inside them
# is notation, not punctuation.
_MATH_SPAN_RE = re.compile(
    r"(\$\$.*?\$\$|\$[^$\n]*\$|\\\(.*?\\\)|\\\[.*?\\\])",
    re.DOTALL,
)

# Words that carry no claim of their own.  A clause is judged on its content
# tokens; requiring the source to also contain "therefore" or "which" would
# reject supported material for stylistic reasons.
_STOPWORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "also", "an",
    "and", "any", "are", "as", "at", "be", "because", "been", "before",
    "being", "below", "between", "both", "but", "by", "can", "cannot",
    "could", "did", "do", "does", "doing", "done", "down", "during", "each",
    "either", "else", "even", "ever", "every", "few", "for", "from",
    "further", "had", "has", "have", "having", "he", "hence", "her", "here",
    "hers", "herself", "him", "himself", "his", "how", "however", "i", "if",
    "in", "into", "is", "it", "its", "itself", "just", "may", "me", "might",
    "more", "most", "much", "must", "my", "myself", "neither", "no", "nor",
    "not", "now", "of", "off", "on", "once", "one", "only", "or", "other",
    "otherwise", "ought", "our", "ours", "ourselves", "out", "over", "own",
    "rather", "same", "shall", "she", "should", "so", "some", "such", "than",
    "that", "the", "their", "theirs", "them", "themselves", "then", "there",
    "therefore", "these", "they", "this", "those", "through", "thus", "to",
    "too", "under", "until", "up", "very", "was", "we", "were", "what",
    "when", "where", "whether", "which", "while", "who", "whom", "why",
    "will", "with", "would", "you", "your", "yours", "yourself",
})

# Suffix folding keeps "increases"/"increasing"/"increase" one token without
# pulling in a stemmer dependency.  Order matters: longest suffix first.
_SUFFIXES = ("ations", "ation", "ingly", "ising", "izing", "ised", "ized",
             "ing", "ies", "ied", "ers", "er", "est", "ed", "es", "ly", "s")


class EvidenceNarrowingError(RuntimeError):
    """Raised only for programmer error, never for unsupported content."""


@dataclass(frozen=True)
class NarrowedRow:
    """One row's disposition, reportable in the run log and the workbook."""

    concept_title: str
    topic: str
    kind: str
    kept_clauses: tuple[str, ...] = ()
    dropped_clauses: tuple[str, ...] = ()
    evidence_block_ids: tuple[str, ...] = ()
    note: str = ""

    @property
    def changed(self) -> bool:
        return self.kind != "unchanged"


@dataclass
class NarrowingAudit:
    """What the disposition did across a whole candidate."""

    version: str = DISPOSITION_VERSION
    rows: list[NarrowedRow] = field(default_factory=list)

    @property
    def changed_rows(self) -> list[NarrowedRow]:
        return [row for row in self.rows if row.changed]

    def summary(self) -> str:
        if not self.changed_rows:
            return "no concept required evidence narrowing"
        narrowed = [row for row in self.changed_rows if row.kind == "narrowed"]
        restated = [row for row in self.changed_rows if row.kind == "restated"]
        parts: list[str] = []
        if narrowed:
            parts.append(
                f"{len(narrowed)} concept(s) narrowed to their supported "
                "clauses"
            )
        if restated:
            parts.append(
                f"{len(restated)} concept(s) restated verbatim from canonical "
                "source text"
            )
        return "; ".join(parts)


# --------------------------------------------------------------------------- #
# Text handling
# --------------------------------------------------------------------------- #

def _normal(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def _drop_silent_e(stem: str) -> str:
    """Fold "increase"/"increases" onto one root.

    Suffix stripping alone leaves them apart: "increases" loses "es" and
    becomes "increas", while "increase" ends in "se" and matches no suffix at
    all.  Dropping a trailing "e" from both sides closes that gap, which
    matters because a textbook writes the verb and a concept writes the noun.
    """

    return stem[:-1] if len(stem) > 3 and stem.endswith("e") else stem


def _fold(token: str) -> str:
    """Return a crude morphological root so inflections compare equal."""

    lowered = token.casefold()
    if lowered.isdigit():
        return lowered
    for suffix in _SUFFIXES:
        if len(lowered) > len(suffix) + 2 and lowered.endswith(suffix):
            stem = lowered[: -len(suffix)]
            if suffix == "ies":
                return stem + "y"
            return _drop_silent_e(stem)
    return _drop_silent_e(lowered)


def content_tokens(text: str) -> set[str]:
    """Return the claim-bearing tokens of ``text``.

    Stopwords and one- or two-character words are dropped: they express
    grammar rather than content, and demanding the source repeat them would
    reject supported material over phrasing.
    """

    out: set[str] = set()
    for match in _TOKEN_RE.finditer(str(text or "")):
        raw = match.group(0)
        lowered = raw.casefold()
        if lowered in _STOPWORDS:
            continue
        if len(lowered) < 3 and not lowered.isdigit():
            continue
        out.add(_fold(lowered))
    return out


def split_clauses(text: str) -> list[str]:
    """Split a Description into ordered clause units.

    Maths spans are masked first so that a decimal point or a ``\\[ ... \\]``
    body cannot be mistaken for a sentence boundary.
    """

    text = _normal(text)
    if not text:
        return []

    spans: list[str] = []

    def _mask(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return f"\x00{len(spans) - 1}\x00"

    masked = _MATH_SPAN_RE.sub(_mask, text)
    # Keep the terminator with its clause so restored text reads naturally.
    parts = _SENTENCE_BREAK_RE.sub(lambda m: m.group(1) + "\x01", masked)

    def _unmask(value: str) -> str:
        return re.sub(
            r"\x00(\d+)\x00",
            lambda m: spans[int(m.group(1))],
            value,
        )

    clauses = [
        _normal(_unmask(part))
        for part in parts.split("\x01")
    ]
    return [clause for clause in clauses if clause]


def _sentences(text: str) -> list[str]:
    """Split source text into candidate restatement units."""

    return [
        clause
        for clause in split_clauses(text)
        if len(content_tokens(clause)) >= 3
    ]


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class TopicEvidence:
    """The canonical text a topic may be grounded against."""

    topic_id: str
    topic_title: str
    blocks: tuple[tuple[str, str], ...] = ()

    @property
    def tokens(self) -> set[str]:
        out: set[str] = set()
        for _block_id, text in self.blocks:
            out |= content_tokens(text)
        return out

    @property
    def block_ids(self) -> tuple[str, ...]:
        return tuple(block_id for block_id, _text in self.blocks)

    @property
    def empty(self) -> bool:
        return not self.blocks


_EXCLUDED_BLOCK_KINDS = frozenset({"layout", "heading", "navigation"})


def build_evidence(
    graph: Mapping[str, Any] | None,
    canonical: Mapping[str, Any] | None,
) -> dict[str, TopicEvidence]:
    """Index every topic's usable canonical block text by ``topic_id``.

    Both the graph and the canonical bundle are read defensively: this
    function runs on the terminal path, where refusing to work because a
    field is shaped unexpectedly would reintroduce the stop it exists to
    remove.
    """

    if not isinstance(graph, Mapping):
        return {}
    canonical_blocks = {
        str(row.get("block_id") or ""): row
        for row in (canonical or {}).get("blocks") or []
        if isinstance(row, Mapping)
    }
    titles = {
        str(row.get("topic_id") or ""): str(row.get("title") or "")
        for row in graph.get("topics") or []
        if isinstance(row, Mapping)
    }
    collected: dict[str, list[tuple[str, str]]] = {}
    for block in graph.get("blocks") or []:
        if not isinstance(block, Mapping):
            continue
        if str(block.get("kind") or "") in _EXCLUDED_BLOCK_KINDS:
            continue
        topic_id = str(block.get("topic_id") or "")
        block_id = str(block.get("block_id") or "")
        if not topic_id or not block_id:
            continue
        text = _block_text(block, canonical_blocks.get(block_id))
        if not text:
            continue
        collected.setdefault(topic_id, []).append((block_id, text))

    return {
        topic_id: TopicEvidence(
            topic_id=topic_id,
            topic_title=titles.get(topic_id, topic_id),
            blocks=tuple(blocks),
        )
        for topic_id, blocks in collected.items()
    }


def _block_text(
    graph_block: Mapping[str, Any],
    canonical_block: Mapping[str, Any] | None,
) -> str:
    override = graph_block.get("source_override")
    if isinstance(override, Mapping):
        resolved = str(override.get("resolved_text") or "")
        if resolved:
            return _normal(resolved)
    source = canonical_block or {}
    return _normal(
        source.get("display_text")
        or source.get("raw_text")
        or graph_block.get("text")
        or ""
    )


def _evidence_for_row(
    row: Mapping[str, Any],
    evidence: Mapping[str, TopicEvidence],
) -> TopicEvidence | None:
    """Resolve a record to its topic evidence by ID, then by title."""

    topic_id = str(row.get("_semantic_topic_id") or "")
    if topic_id and topic_id in evidence:
        return evidence[topic_id]
    wanted = _normal(row.get("topic")).casefold()
    if not wanted:
        return None
    for candidate in evidence.values():
        if _normal(candidate.topic_title).casefold() == wanted:
            return candidate
    return None


# --------------------------------------------------------------------------- #
# Support test
# --------------------------------------------------------------------------- #

def clause_supported(clause: str, evidence_tokens: Iterable[str]) -> bool:
    """Is every content token of ``clause`` present in the evidence?

    Subset containment is the whole guarantee.  A partially matching clause
    is treated as unsupported, because the part that does not match is
    exactly the over-inference the grounding verifier rejected.
    """

    tokens = content_tokens(clause)
    if len(tokens) < 2:
        # A fragment with at most one content word asserts nothing checkable;
        # keeping it would smuggle unverified text through a subset test that
        # it passes only for being short.
        return False
    return tokens <= set(evidence_tokens)


def _restatement(
    row: Mapping[str, Any],
    topic_evidence: TopicEvidence,
) -> tuple[str, tuple[str, ...]]:
    """Return the best verbatim source sentence for this concept.

    Selection is by content-token overlap with the concept's title and
    original claim, tie-broken by block order and then sentence order, so a
    rerun always chooses the same sentence.
    """

    wanted = content_tokens(
        f"{row.get('concept_title') or row.get('concept') or ''} "
        f"{grounding_certificate.source_claim(row)}"
    )
    best: tuple[int, int, int] | None = None
    best_text = ""
    best_block = ""
    for block_index, (block_id, text) in enumerate(topic_evidence.blocks):
        for sentence_index, sentence in enumerate(_sentences(text)):
            overlap = len(content_tokens(sentence) & wanted)
            if overlap <= 0:
                continue
            # Higher overlap wins; earlier block and sentence break ties.
            key = (-overlap, block_index, sentence_index)
            if best is None or key < best:
                best = key
                best_text = sentence
                best_block = block_id
    if not best_text:
        # No sentence shares a content word with the concept.  Fall back to
        # the topic's first usable sentence rather than inventing wording.
        for block_id, text in topic_evidence.blocks:
            sentences = _sentences(text)
            if sentences:
                best_text = sentences[0]
                best_block = block_id
                break
    if not best_text:
        return "", ()
    return best_text[:_MAX_RESTATEMENT_CHARS].strip(), (best_block,)


# --------------------------------------------------------------------------- #
# Row rewriting
# --------------------------------------------------------------------------- #

def _replace_description(details: str, description: str) -> str:
    sections = cr.split_sections(details)
    replaced = False
    out: list[tuple[str, str]] = []
    for label, content in sections:
        if (
            not replaced
            and str(label or "").strip().casefold().startswith("description")
        ):
            out.append((label, description))
            replaced = True
            continue
        out.append((label, content))
    if not replaced:
        out.insert(0, (_DESCRIPTION_LABEL, description))
    return cr.join_sections(out)


def narrow_row(
    row: Mapping[str, Any],
    topic_evidence: TopicEvidence | None,
) -> tuple[dict[str, Any], NarrowedRow]:
    """Return ``row`` restricted to what ``topic_evidence`` supports.

    The row is always returned.  Only its source-facing Description can
    change; title, topic, parent and every pedagogical section are left
    exactly as the topology decided them.
    """

    out = copy.deepcopy(dict(row))
    title = _normal(out.get("concept_title") or out.get("concept"))
    topic = _normal(out.get("topic"))
    claim = grounding_certificate.source_claim(out)

    if topic_evidence is None or topic_evidence.empty:
        # No canonical text is indexed for this topic.  That is a source or
        # graph defect rather than an over-inference by this concept, so the
        # claim is left untouched and named in the audit.  Nothing is written
        # onto the row: an unchanged concept must stay byte-identical, or a
        # bookkeeping field would perturb downstream identity hashing.
        note = (
            "no canonical source blocks are indexed for this topic; the "
            "claim was left unchanged and reported"
        )
        return out, NarrowedRow(
            concept_title=title,
            topic=topic,
            kind="unchanged",
            note=note,
        )

    tokens = topic_evidence.tokens
    clauses = split_clauses(claim)
    kept = [clause for clause in clauses if clause_supported(clause, tokens)]
    dropped = [clause for clause in clauses if clause not in kept]

    if kept and not dropped:
        return out, NarrowedRow(
            concept_title=title,
            topic=topic,
            kind="unchanged",
            kept_clauses=tuple(kept),
            evidence_block_ids=topic_evidence.block_ids,
        )

    if kept:
        description = " ".join(kept)
        out["concept_details"] = _replace_description(
            str(out.get("concept_details") or out.get("concept_description") or ""),
            description,
        )
        out[NARROWED_FLAG_FIELD] = True
        out[NARROWED_KIND_FIELD] = "narrowed"
        out[NARROWED_DROPPED_FIELD] = list(dropped)
        out[NARROWED_ORIGINAL_FIELD] = claim
        out[NARROWED_EVIDENCE_FIELD] = list(topic_evidence.block_ids)
        out[NARROWED_NOTE_FIELD] = (
            f"{len(dropped)} unsupported clause(s) removed; the concept now "
            "states only what its topic's canonical blocks support"
        )
        return out, NarrowedRow(
            concept_title=title,
            topic=topic,
            kind="narrowed",
            kept_clauses=tuple(kept),
            dropped_clauses=tuple(dropped),
            evidence_block_ids=topic_evidence.block_ids,
            note=out[NARROWED_NOTE_FIELD],
        )

    restatement, blocks = _restatement(out, topic_evidence)
    if not restatement:
        note = (
            "the topic's canonical blocks yielded no usable sentence; the "
            "claim was left unchanged and reported"
        )
        return out, NarrowedRow(
            concept_title=title,
            topic=topic,
            kind="unchanged",
            dropped_clauses=tuple(dropped),
            note=note,
        )

    out["concept_details"] = _replace_description(
        str(out.get("concept_details") or out.get("concept_description") or ""),
        restatement,
    )
    out[NARROWED_FLAG_FIELD] = True
    out[NARROWED_KIND_FIELD] = "restated"
    out[NARROWED_DROPPED_FIELD] = list(dropped)
    out[NARROWED_ORIGINAL_FIELD] = claim
    out[NARROWED_EVIDENCE_FIELD] = list(blocks)
    out[NARROWED_NOTE_FIELD] = (
        "no written clause was supported; the concept was restated verbatim "
        "from its canonical source block"
    )
    return out, NarrowedRow(
        concept_title=title,
        topic=topic,
        kind="restated",
        kept_clauses=(restatement,),
        dropped_clauses=tuple(dropped),
        evidence_block_ids=blocks,
        note=out[NARROWED_NOTE_FIELD],
    )


def narrow_records(
    records: Sequence[Mapping[str, Any]],
    *,
    graph: Mapping[str, Any] | None = None,
    canonical: Mapping[str, Any] | None = None,
    evidence: Mapping[str, TopicEvidence] | None = None,
    concept_titles: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], NarrowingAudit]:
    """Apply the terminal disposition across a candidate.

    ``concept_titles`` restricts the rewrite to the concepts a grounding
    diagnostic actually named; omit it to consider every row.  Rows outside
    the selection are returned untouched, and no row is ever removed.
    """

    if evidence is None:
        evidence = build_evidence(graph, canonical)
    wanted = (
        {_normal(title).casefold() for title in concept_titles}
        if concept_titles is not None
        else None
    )

    audit = NarrowingAudit()
    out: list[dict[str, Any]] = []
    for row in records:
        if not isinstance(row, Mapping):
            out.append(copy.deepcopy(row))
            continue
        title = _normal(row.get("concept_title") or row.get("concept"))
        if wanted is not None and title.casefold() not in wanted:
            out.append(copy.deepcopy(dict(row)))
            continue
        narrowed, report = narrow_row(row, _evidence_for_row(row, evidence))
        out.append(narrowed)
        audit.rows.append(report)

    if len(out) != len(records):  # pragma: no cover - defensive
        raise EvidenceNarrowingError(
            "evidence narrowing must return one row per input record"
        )
    return out, audit
