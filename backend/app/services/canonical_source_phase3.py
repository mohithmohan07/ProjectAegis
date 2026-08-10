"""Phase 3 API-first universal semantic source graph for Build Concepts.

Phase 2 made the ACSD task ledger authoritative while semantic concept writing
still consumed flat MMD.  Phase 3 introduces one stable graph between source
conversion and generation:

* the original PDF, Mathpix MMD, PDF text layer, and verified GPT page ACSD are
  evidence channels rather than competing sources of truth;
* textbook structure is represented by stable topic, subtopic, block, task,
  Figure, and math IDs;
* API calls interpret hierarchy and concept grounding only within deterministic
  ID/evidence boundaries;
* source order, exact task wording, QIDs, images, KaTeX, and final rendering
  remain deterministic.

The module is deliberately usable without a live provider.  Conversion writes a
fully deterministic graph shadow.  At generation time, live deployments enrich
and independently audit that graph before it becomes the semantic source.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import os
import re
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from .. import config
from . import canonical_source
from . import canonical_source_phase2 as phase2
from . import canonical_source_phase21_structure as structure
from . import canonical_source_phase22 as phase22
from . import canonical_source_phase221_fallback as page_acsd
from . import katex_rules as kr
from . import progress
from . import semantic_confidence_policy as confidence_policy
from . import semantic_recovery

PHASE = "phase-3-semantic-graph"
SCHEMA_NAME = "Aegis Universal Semantic Source Graph"
SCHEMA_VERSION = "3.0.0"
COMPILER_VERSION = "phase-3-semantic-graph-2"

GRAPH_FILENAME = "source.semantic-graph.json"
GRAPH_REPORT_FILENAME = "source.semantic-graph-report.json"
SEMANTIC_SOURCE_FILENAME = "source.semantic.mmd"
VISION_ACSD_FILENAME = "source.phase3-page-acsd.json"

ARTIFACT_SPECS: dict[str, dict[str, str]] = {
    "semantic_graph": {
        "filename": GRAPH_FILENAME,
        "media_type": "application/json; charset=utf-8",
        "label": "Phase 3 semantic source graph",
    },
    "semantic_graph_report": {
        "filename": GRAPH_REPORT_FILENAME,
        "media_type": "application/json; charset=utf-8",
        "label": "Phase 3 semantic graph report",
    },
    "semantic_source": {
        "filename": SEMANTIC_SOURCE_FILENAME,
        "media_type": "text/markdown; charset=utf-8",
        "label": "Phase 3 deterministic semantic source",
    },
    "phase3_page_acsd": {
        "filename": VISION_ACSD_FILENAME,
        "media_type": "application/json; charset=utf-8",
        "label": "Phase 3 verified PDF page evidence",
    },
}

_ACTIVE_GRAPH: ContextVar[dict[str, Any] | None] = ContextVar(
    "aegis_phase3_active_semantic_graph", default=None
)
_ACTIVE_SESSION: ContextVar[dict[str, Any] | None] = ContextVar(
    "aegis_phase3_active_session", default=None
)
_HUMAN_SOURCE_RESOLUTIONS: ContextVar[Any] = ContextVar(
    "aegis_phase3_human_source_resolutions", default=None
)

_SOURCE_REVIEW_VERSION = "phase3-source-review-1"
_SOURCE_REVIEW_KEY = "human_source_review"
_DOWNSTREAM_INVALIDATION_KEY = "downstream_invalidation_required"
_MACHINE_METADATA_MIGRATION_KEY = "machine_metadata_sanitization"
_MACHINE_METADATA_MIGRATION_VERSION = 1
_ADJUDICATED_HEADING_MIGRATION_KEY = (
    "adjudicated_heading_validation_migration"
)
_ADJUDICATED_HEADING_MIGRATION_VERSION = 1
_NUMBERED_TOPIC_PATCH_VERSION = "phase3-canonical-topic-patch-1"

_SPACE_RE = re.compile(r"\s+")
_NUMBER_PREFIX_RE = re.compile(
    r"^\s*(?:chapter\s+)?(?P<number>\d+(?:[.．]\d+)*)\s*"
    r"(?:[-:–—]\s*|\s+)?(?P<title>.*)$",
    re.IGNORECASE,
)
_MAIN_NUMBER_RE = re.compile(r"^\d+$")
_SUB_NUMBER_RE = re.compile(r"^(?P<major>\d+)[.．](?P<minor>\d+)(?:[.．]\d+)*$")
_AUXILIARY_TITLE_RE = re.compile(
    r"^(?:new\s+words?|glossary|activity|activities|discuss|project|"
    r"write\s+in\s+brief|exercise(?:s)?(?:\s+\d+(?:[.．]\d+)*)?|"
    r"source(?:\s+[a-z0-9]+)?|box(?:\s+\d+)?|some\s+important\s+dates|"
    r"summary|recap|review|learning\s+outcomes?|objectives?)\b",
    re.IGNORECASE,
)
_SOURCE_TITLE_RE = re.compile(r"^(?:source|case\s+study|extract|passage)\b", re.I)
_GLOSSARY_TITLE_RE = re.compile(r"^(?:new\s+words?|glossary|vocabulary)\b", re.I)
_ACTIVITY_TITLE_RE = re.compile(
    r"^(?:activity|activities|discuss|think\s+about\s+it|let'?s\s+discuss|project)\b",
    re.I,
)
_EXERCISE_TITLE_RE = re.compile(
    r"^(?:write\s+in\s+brief|exercise|exercises|questions?|review)\b", re.I
)
_BOX_TITLE_RE = re.compile(r"^(?:box|info\s+box|did\s+you\s+know)\b", re.I)
_LAYOUT_COMMAND_RE = re.compile(
    r"\\(?:captionsetup|caption|includegraphics|label|centering)\b(?:\[[^\]]*\])?"
    r"(?:\{[^{}]*\})?",
    re.IGNORECASE,
)
_ENV_RE = re.compile(
    r"\\(?:begin|end)\{(?:figure\*?|table\*?|tabular\*?|itemize|enumerate)\}"
    r"(?:\{[^{}]*\})?",
    re.IGNORECASE,
)
_LIST_ITEM_RE = re.compile(r"\\item(?:\[(?P<label>[^\]]+)\])?\s*", re.IGNORECASE)
_TABLE_BEGIN_RE = re.compile(r"\\begin\{tabular\}\{[^{}]*\}", re.IGNORECASE)
_SUSPICIOUS_MARKUP_RE = re.compile(
    r"<\s*/?\s*(?:smiles|chem|molecule|reaction|structure|mathml)\b",
    re.IGNORECASE,
)
_MARKDOWN_IMAGE_ANY_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(\s*(?P<src>https://[^)\s]+)\s*\)",
    re.IGNORECASE,
)
_CANONICAL_IMAGE_ANY_RE = re.compile(r"\[img\b[^\]]*\]", re.IGNORECASE)
_CANONICAL_KATEX_ANY_RE = re.compile(
    r"\[katex\]\s*.*?\s*\[/katex\]", re.IGNORECASE | re.DOTALL
)
_INCLUDEGRAPHICS_ANY_RE = re.compile(
    r"\\includegraphics(?:\[[^\]]*\])?\{(?P<src>https://[^}\s]+)\}",
    re.IGNORECASE,
)
_BARE_HTTPS_RE = re.compile(r"https://[^\s<>\[\]]+", re.IGNORECASE)
_HTML_COMMENT_RE = re.compile(r"<!--(?P<body>.*?)-->", re.DOTALL)
_MACHINE_METADATA_KEYS = {
    "compilation_status",
    "compiler_version",
    "schema_version",
    "source_origin",
    "source_sha256",
    "used_for_generation",
}
_EMPTY_MATH_DELIMITER_PATTERNS = (
    re.compile(r"\\\[\s*\\\]", re.DOTALL),
    re.compile(r"\\\(\s*\\\)", re.DOTALL),
    re.compile(r"(?<!\\)\$\$\s*(?<!\\)\$\$", re.DOTALL),
    re.compile(r"(?<!\\)\$\s*(?<!\\)\$", re.DOTALL),
)

HierarchyProvider = Callable[[dict[str, Any]], dict[str, Any]]
HierarchyCritic = Callable[[dict[str, Any]], dict[str, Any]]
GroundingProvider = Callable[[dict[str, Any]], dict[str, Any]]
GroundingCritic = Callable[[dict[str, Any]], dict[str, Any]]
TopicResolutionProvider = Callable[[dict[str, Any]], dict[str, Any]]
TopicResolutionCritic = Callable[[dict[str, Any]], dict[str, Any]]
AnomalyProvider = Callable[[dict[str, Any]], dict[str, Any]]
AnomalyCritic = Callable[[dict[str, Any]], dict[str, Any]]

_VISUAL_MARKER_RE = re.compile(r"\[\[VISUAL:(\d+)\]\]", re.IGNORECASE)
_WORD_TOKEN_RE = re.compile(r"[^\W_]{3,}", re.UNICODE)


def _normal(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip().casefold()


def _semantic_title_key(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"\\(?:mathbf|boldsymbol|mathrm|text|operatorname)\s*\{([^{}]*)\}", r" \1 ", text)
    text = re.sub(r"\\[A-Za-z]+\*?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    number, title = _title_number(text) if "_title_number" in globals() else ("", text)
    if number and title:
        text = title
    else:
        text = re.sub(r"^\s*\d+(?:[.．]\d+)*\s+", "", text)
    return _normal(text)


def _plain_title(
    value: object,
    *,
    strip_machine_metadata: bool = True,
) -> str:
    """Render a source heading as plain learner-visible text.

    Topic identity remains tied to the source section ID; this helper removes
    only presentation TeX such as ``\boldsymbol{n}`` and structural numbering.
    It never invents or translates heading text.
    """
    text = str(value or "")
    if strip_machine_metadata:
        text = _strip_machine_metadata_comments(text)
    text = html.unescape(text).strip()
    text = re.sub(
        r"\\(?:mathbf|boldsymbol|mathrm|mathit|text|operatorname)\s*\{([^{}]*)\}",
        r"\1",
        text,
    )
    text = re.sub(r"\\(?:section|subsection|subsubsection|chapter)\*?\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+\*?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    number, title = _title_number(text) if "_title_number" in globals() else ("", text)
    if number and title:
        text = title
    else:
        text = re.sub(r"^\s*(?:chapter\s+)?\d+(?:[.．]\d+)*\s+", "", text, flags=re.I)
    return _SPACE_RE.sub(" ", text).strip(" -:–—\t\n")


def _sha256_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256_text(payload)


@contextmanager
def human_source_resolution_context(resolutions: Any) -> Iterator[None]:
    """Expose durable source-review answers to one explicit resume attempt."""

    token = _HUMAN_SOURCE_RESOLUTIONS.set(copy.deepcopy(resolutions))
    try:
        yield
    finally:
        _HUMAN_SOURCE_RESOLUTIONS.reset(token)


def _resolution_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        row = dict(value)
        if str(row.get("choice") or "").strip():
            return [row]
        result: list[dict[str, Any]] = []
        for key, raw in value.items():
            if not isinstance(raw, Mapping):
                continue
            candidate = dict(raw)
            candidate.setdefault("_lookup_key", str(key))
            result.append(candidate)
        return result
    if isinstance(value, (list, tuple)):
        result: list[dict[str, Any]] = []
        for raw in value:
            result.extend(_resolution_rows(raw))
        return result
    return []


def _human_source_resolution(
    *,
    decision_id: str,
    context_hash: str,
) -> dict[str, Any] | None:
    """Return only a saved answer for this exact immutable review context."""

    for candidate in reversed(
        _resolution_rows(_HUMAN_SOURCE_RESOLUTIONS.get())
    ):
        if (
            str(candidate.get("decision_id") or "") != decision_id
            or str(candidate.get("context_hash") or "") != context_hash
        ):
            continue
        choice = str(candidate.get("choice") or "").strip()
        if choice not in {
            "accept_recommended",
            "select_candidate",
            "replace_source",
            "custom_instruction",
            # A carried decision is an answer -- "Aegis settled this by
            # changing nothing". Dropping it here would make the answer
            # invisible, so this review would be raised again on the next pass
            # and carried again, forever. The caller applies nothing for it.
            "carry_forward",
        }:
            continue
        return copy.deepcopy(candidate)
    return None


def _atomic_write(path: Path, content: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def enabled() -> bool:
    return os.environ.get("AEGIS_PHASE3_ENABLED", "1").strip().lower() not in {
        "0", "false", "no", "off"
    }


def semantic_api_enabled() -> bool:
    configured = os.environ.get("AEGIS_PHASE3_SEMANTIC_API_ENABLED", "1")
    return (
        enabled()
        and configured.strip().lower() not in {"0", "false", "no", "off"}
        and config.use_live_generation()
    )


def vision_enabled() -> bool:
    configured = os.environ.get("AEGIS_PHASE3_PDF_VISION_ENABLED", "1")
    return (
        semantic_api_enabled()
        and configured.strip().lower() not in {"0", "false", "no", "off"}
    )


def subject_adapter(subject: object) -> str:
    """Return the universal adapter without generic ``Science`` shadowing.

    The order is intentional: specialised names such as Computer Science,
    Social Science, and Life Science must resolve before the generic fallback.
    """
    value = _normal(subject)
    if any(token in value for token in ("mathematics", "maths", "math")):
        return "mathematics"
    if any(token in value for token in (
        "computer science", "computer applications", "informatics", "coding",
        "artificial intelligence", "machine learning", "data science",
    )):
        return "computer_science"
    if any(token in value for token in ("electronics", "electrical technology")):
        return "electronics"
    if any(token in value for token in (
        "biology", "life science", "botany", "zoology", "biotechnology",
    )):
        return "life_science"
    if any(token in value for token in (
        "environmental science", "environmental studies", "evs", "ecology",
    )):
        return "environmental_science"
    if any(token in value for token in (
        "physics", "chemistry", "physical science", "physical sciences",
    )):
        return "physical_science"
    if any(token in value for token in (
        "social science", "social studies", "history", "geography", "civics",
        "political science", "sociology",
    )):
        return "social_science"
    if any(token in value for token in (
        "commerce", "economics", "commercial studies", "accountancy", "business",
    )):
        return "commerce"
    if any(token in value for token in (
        "english", "language", "literature", "kannada", "hindi", "sanskrit",
        "french", "german", "urdu", "tamil", "telugu", "malayalam",
    )):
        return "language_literature"
    if any(token in value for token in (
        "physical education", "health education", "yoga", "sports",
    )):
        return "health_physical_education"
    if any(token in value for token in (
        "art", "music", "dance", "craft", "theatre", "drama",
    )):
        return "arts"
    if "science" in value:
        return "general_science"
    return "general"


def source_contract_hash(canonical: dict[str, Any]) -> str:
    """Hash every identity-bearing canonical element and verified overlay."""
    payload = {
        "compiler": canonical.get("compiler_version"),
        "schema": canonical.get("schema_version"),
        "document": canonical.get("document"),
        "section_sequence": canonical.get("section_sequence"),
        "sections": [
            {
                "section_id": row.get("section_id"),
                "order": row.get("order"),
                "parent_section_id": row.get("parent_section_id"),
                "level": row.get("level"),
                "title": row.get("title"),
                "source_start": row.get("source_start"),
                "source_end": row.get("source_end"),
                "adjudicated_heading": row.get("adjudicated_heading"),
            }
            for row in canonical.get("sections") or []
            if isinstance(row, dict)
        ],
        "blocks": [
            {
                "block_id": row.get("block_id"),
                "order": row.get("order"),
                "kind": row.get("kind"),
                "section_id": row.get("section_id"),
                "source_start": row.get("source_start"),
                "source_end": row.get("source_end"),
                "raw_sha256": row.get("raw_sha256"),
                "figure_id": row.get("figure_id"),
                "task_ids": row.get("task_ids"),
            }
            for row in canonical.get("blocks") or []
            if isinstance(row, dict)
        ],
        "tasks": [
            {
                "task_id": row.get("task_id"),
                "qid": row.get("qid"),
                "order": row.get("order"),
                "identity_key": row.get("identity_key"),
                "section_id": row.get("section_id"),
                "source_start": row.get("source_start"),
                "source_end": row.get("source_end"),
                "figure_refs": row.get("figure_refs"),
                "display_prompt": row.get("display_prompt"),
            }
            for row in canonical.get("tasks") or []
            if isinstance(row, dict)
        ],
        "figures": canonical.get("figures") or [],
        "math": canonical.get("math") or [],
        "source_overlays": canonical.get("source_overlays") or [],
        "source_adjudication": canonical.get("source_adjudication") or {},
    }
    return _sha256_json(payload)


def semantic_context_hash(metadata: dict[str, Any] | None) -> str:
    """Hash the user-selected academic context that can affect semantics."""
    metadata = dict(metadata or {})
    identity = {
        key: str(metadata.get(key) or "").strip()
        for key in (
            "board",
            "grade",
            "subject",
            "unit",
            "chapter_title",
            "chapter_id",
            "chapter_code",
            "learning_kind",
        )
    }
    identity["subject_adapter"] = subject_adapter(identity["subject"])
    return _sha256_json(identity)


def _title_number(title: object, raw: object = "") -> tuple[str, str]:
    text = _SPACE_RE.sub(" ", str(title or "")).strip()
    source = str(raw or "")
    candidates = [text]
    latex = re.search(
        r"\\(?:chapter|section|subsection|subsubsection)\*?\{\s*([^}]+)\}",
        source,
        flags=re.IGNORECASE,
    )
    if latex:
        candidates.insert(0, latex.group(1).strip())
    markdown = re.search(r"(?m)^#{1,6}\s+(.+?)\s*$", source)
    if markdown:
        candidates.insert(0, markdown.group(1).strip())
    for candidate in candidates:
        match = _NUMBER_PREFIX_RE.match(candidate)
        if not match:
            continue
        number = (match.group("number") or "").replace("．", ".")
        remainder = (match.group("title") or "").strip()
        if number and remainder:
            return number, remainder
    return "", text


def _baseline_section_role(
    section: dict[str, Any],
    *,
    chapter_title: str,
    numbered_main_section_ids: set[str],
    numbered_sub_section_ids: set[str],
    has_numbered_mains: bool,
) -> str:
    section_id = str(section.get("section_id") or "")
    title = str(section.get("title") or "").strip()
    title_key = _normal(title)
    if not title or str(section.get("heading_kind") or "") == "implicit_preamble":
        return "content_heading"
    if section_id in numbered_main_section_ids:
        return "main_topic"
    if section_id in numbered_sub_section_ids:
        return "subtopic"
    if title_key and title_key == _normal(chapter_title):
        return "chapter_heading"
    if _GLOSSARY_TITLE_RE.match(title):
        return "glossary"
    if _SOURCE_TITLE_RE.match(title):
        return "source_extract"
    if _ACTIVITY_TITLE_RE.match(title):
        return "activity"
    if _EXERCISE_TITLE_RE.match(title):
        return "exercise"
    if _BOX_TITLE_RE.match(title):
        return "box"
    if _AUXILIARY_TITLE_RE.match(title):
        return "other"
    try:
        level = max(1, int(section.get("level") or 1))
    except (TypeError, ValueError):
        level = 1
    if has_numbered_mains:
        return "content_heading"
    if level == 1 and not _AUXILIARY_TITLE_RE.match(title):
        return "main_topic"
    if level > 1:
        return "subtopic"
    return "content_heading"


def _section_excerpt(
    section: dict[str, Any], blocks_by_id: dict[str, dict[str, Any]], limit: int = 900
) -> str:
    pieces: list[str] = []
    for block_id in section.get("block_ids") or []:
        block = blocks_by_id.get(str(block_id))
        if not block or block.get("kind") in {"layout", "heading"}:
            continue
        text = str(block.get("display_text") or block.get("raw_text") or "").strip()
        if text:
            pieces.append(_SPACE_RE.sub(" ", text))
        if sum(len(piece) for piece in pieces) >= limit:
            break
    return " ".join(pieces)[:limit]


def _vision_heading_evidence(page_bundle: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(page_bundle, dict):
        return out
    for page in page_bundle.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("page_number") or 0)
        for block in page.get("blocks") or []:
            if not isinstance(block, dict) or block.get("kind") != "heading":
                continue
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            number, title = _title_number(text)
            out.append({
                "page_id": str(page.get("page_id") or ""),
                "page_number": page_number,
                "reading_order": int(block.get("reading_order") or 0),
                "heading_level": int(block.get("heading_level") or 1),
                "text": text,
                "number": number,
                "title": title,
                "confidence": float(block.get("confidence") or 0.0),
            })
    return out


def _virtual_missing_main_candidates(
    canonical: dict[str, Any], page_bundle: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Use verified page evidence only for provably missing numbered parents."""
    mains, subsections = structure.numbered_heading_inventory(canonical)
    missing = sorted(major for major in subsections if major not in mains)
    if not missing:
        return []
    evidence = _vision_heading_evidence(page_bundle)
    virtual: list[dict[str, Any]] = []
    for number in missing:
        matches = [
            row for row in evidence
            if row.get("number") == str(number)
            and _MAIN_NUMBER_RE.fullmatch(str(row.get("number") or ""))
            and confidence_policy.accepts(
                row.get("confidence"),
                confidence_policy.ConfidenceGate.SOURCE_CRITICAL,
            )
        ]
        titles = {_normal(row.get("title")) for row in matches if row.get("title")}
        if len(matches) != 1 or len(titles) != 1:
            continue
        first_sub_start = min(
            int(block.get("source_start") or 0) for block in subsections[number]
        )
        row = matches[0]
        virtual.append({
            "section_id": f"VISION-SECTION-{number:04d}",
            "order": 0,
            "parent_section_id": "",
            "level": 1,
            "depth": 1,
            "title": str(row.get("title") or "").strip(),
            "source_start": first_sub_start,
            "source_end": first_sub_start,
            "block_ids": [],
            "phase3_virtual": True,
            "vision_evidence": copy.deepcopy(row),
            "number": str(number),
        })
    return virtual


def _numbered_main_binding_rows(
    canonical: dict[str, Any],
    sections: list[dict[str, Any]] | None = None,
    *,
    materialize_projections: bool = False,
) -> list[dict[str, Any]]:
    """Bind numbered heading evidence to one stable semantic section.

    Phase 2 source blocks occasionally retain a stale ``section_id`` after a
    verified overlay or PDF-to-ACSD turnover.  The numbered heading itself is
    still exact canonical evidence, so Phase 3 first checks the claimed
    section, then an exact-title/source-position match, and finally projects a
    graph-only section from the canonical heading.  No raw MMD or ACSD record
    is mutated.
    """

    working_sections = sections if sections is not None else [
        copy.deepcopy(row)
        for row in canonical.get("sections") or []
        if isinstance(row, dict) and str(row.get("section_id") or "")
    ]
    by_id = {
        str(row.get("section_id") or ""): row
        for row in working_sections
        if isinstance(row, dict) and str(row.get("section_id") or "")
    }
    mains, _subsections = structure.numbered_heading_inventory(canonical)
    bindings: list[dict[str, Any]] = []
    for number, block in sorted(mains.items()):
        raw_text = str(block.get("raw_text") or "")
        heading_title = str(
            (block.get("heading") or {}).get("title") or ""
        ).strip()
        _raw_number, raw_title = _title_number(heading_title, raw_text)
        expected_title = _plain_title(raw_title or heading_title or raw_text)
        expected_key = _semantic_title_key(expected_title)
        claimed_id = str(block.get("section_id") or "")
        claimed = by_id.get(claimed_id)
        resolved: dict[str, Any] | None = None
        mode = ""
        if (
            isinstance(claimed, dict)
            and expected_key
            and _semantic_title_key(claimed.get("title")) == expected_key
        ):
            resolved = claimed
            mode = "claimed_section"
        if resolved is None and expected_key:
            exact = [
                row for row in working_sections
                if isinstance(row, dict)
                and str(row.get("section_id") or "")
                and _semantic_title_key(row.get("title")) == expected_key
            ]
            if exact:
                position = int(block.get("source_start") or 0)

                def rank(row: dict[str, Any]) -> tuple[int, int]:
                    start = int(row.get("source_start") or 0)
                    end = int(row.get("source_end") or start)
                    contains = start <= position <= max(start, end)
                    return (0 if contains else 1, abs(start - position))

                ranked = sorted(exact, key=lambda row: (
                    *rank(row), str(row.get("section_id") or "")
                ))
                best_rank = rank(ranked[0])
                best = [row for row in ranked if rank(row) == best_rank]
                if len(best) == 1:
                    resolved = best[0]
                    mode = "exact_title_position_rebind"
        if resolved is None:
            source_start = int(block.get("source_start") or 0)
            source_end = max(
                source_start,
                int(block.get("source_end") or source_start),
            )
            projection_key = _sha256_json({
                "version": _NUMBERED_TOPIC_PATCH_VERSION,
                "number": int(number),
                "block_id": str(block.get("block_id") or ""),
                "title": expected_title,
                "source_start": source_start,
            })
            projection_id = (
                f"PHASE3-NUMBERED-{int(number):04d}-"
                f"{projection_key[:12].upper()}"
            )
            resolved = by_id.get(projection_id)
            if not isinstance(resolved, dict):
                resolved = {
                    "section_id": projection_id,
                    "order": int(block.get("order") or 0),
                    "parent_section_id": "",
                    "level": 1,
                    "depth": 1,
                    "title": f"{number} {expected_title}".strip(),
                    "source_start": source_start,
                    "source_end": source_end,
                    "block_ids": [str(block.get("block_id") or "")],
                    "phase3_numbered_projection": True,
                    "number": str(number),
                }
                if materialize_projections:
                    working_sections.append(resolved)
                    by_id[projection_id] = resolved
            mode = "canonical_heading_projection"
        bindings.append({
            "number": str(number),
            "block_id": str(block.get("block_id") or ""),
            "claimed_section_id": claimed_id,
            "resolved_section_id": str(resolved.get("section_id") or ""),
            "expected_title": expected_title,
            "source_start": int(block.get("source_start") or 0),
            "resolution_mode": mode,
            "heading_origin": (
                "adjudicated_pdf"
                if block.get("adjudicated_heading") is True
                else "canonical_block"
            ),
            "heading_materialized_in_blocks": not bool(
                block.get("adjudicated_heading") is True
            ),
        })
    return bindings


def _verified_adjudicated_heading_binding(
    binding: Mapping[str, Any],
    *,
    canonical: Mapping[str, Any],
) -> bool:
    """Verify one PDF-recovered heading without inventing a graph block.

    Phase 2.2 deliberately keeps recovered parent headings outside the raw
    canonical block inventory.  They may stand in for a physical heading block
    only when the section metadata, overlay, and verified adjudication ledger
    all seal the same number, title, repair, source offset, and PDF page.
    """

    if (
        str(binding.get("heading_origin") or "") != "adjudicated_pdf"
        or binding.get("heading_materialized_in_blocks") is not False
    ):
        return False
    number = str(binding.get("number") or "")
    section_id = str(binding.get("resolved_section_id") or "")
    expected_title = str(binding.get("expected_title") or "")
    if (
        not number.isdigit()
        or not section_id
        or str(binding.get("claimed_section_id") or "") != section_id
        or str(binding.get("block_id") or "")
        != f"ADJUDICATED-{section_id}"
    ):
        return False
    section = next(
        (
            row
            for row in canonical.get("sections") or []
            if isinstance(row, Mapping)
            and str(row.get("section_id") or "") == section_id
        ),
        None,
    )
    if not isinstance(section, Mapping):
        return False
    adjudicated = section.get("adjudicated_heading")
    if not isinstance(adjudicated, Mapping):
        return False
    repair_id = str(adjudicated.get("repair_id") or "")
    raw_text = str(adjudicated.get("raw_text") or "")
    try:
        section_number = int(adjudicated.get("section_number") or 0)
        page_number = int(adjudicated.get("page_number") or 0)
    except (TypeError, ValueError):
        return False
    _raw_number, raw_title = _title_number(
        adjudicated.get("title"), raw_text)
    adjudicated_main_sections = canonical.get("adjudicated_main_sections")
    if not isinstance(adjudicated_main_sections, Mapping):
        return False
    if (
        section_number != int(number)
        or page_number <= 0
        or not repair_id
        or _semantic_title_key(raw_title or adjudicated.get("title"))
        != _semantic_title_key(expected_title)
        or _semantic_title_key(section.get("title"))
        != _semantic_title_key(expected_title)
        or str(adjudicated_main_sections.get(number) or "") != section_id
    ):
        return False
    overlay = next(
        (
            row
            for row in canonical.get("source_overlays") or []
            if isinstance(row, Mapping)
            and str(row.get("repair_id") or "") == repair_id
            and str(row.get("kind") or "") == "missing_parent_heading"
        ),
        None,
    )
    overlay_text = str((overlay or {}).get("text") or "")
    try:
        raw_overlay_offset = (
            overlay.get("offset") if isinstance(overlay, Mapping) else None
        )
        overlay_offset = (
            int(raw_overlay_offset)
            if raw_overlay_offset is not None
            else -1
        )
        section_start = int(section.get("source_start") or 0)
    except (TypeError, ValueError):
        return False
    if (
        not isinstance(overlay, Mapping)
        or overlay_offset != section_start
        or str(overlay.get("text_sha256") or "")
        != _sha256_text(overlay_text)
        or not raw_text
        or raw_text not in overlay_text
    ):
        return False
    marker = canonical.get("source_adjudication")
    if not isinstance(marker, Mapping) or (
        str(marker.get("version") or "") != phase22.ADJUDICATION_VERSION
        or str(marker.get("status") or "") != "verified"
        or marker.get("raw_mmd_changed") is not False
    ):
        return False
    decision = next(
        (
            row
            for row in marker.get("decisions") or []
            if isinstance(row, Mapping)
            and str(row.get("issue_type") or "")
            == "missing_parent_section"
            and str(row.get("status") or "") == "verified"
            and isinstance(row.get("provenance"), Mapping)
            and str(row["provenance"].get("repair_id") or "") == repair_id
        ),
        None,
    )
    if not isinstance(decision, Mapping):
        return False
    provenance = decision["provenance"]
    try:
        provenance_page = int(provenance.get("page_number") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        provenance.get("raw_mmd_changed") is False
        and provenance_page == page_number
        and str(provenance.get("recovered_text") or "") == raw_text
    )


def _classification_payload(
    canonical: dict[str, Any],
    *,
    metadata: dict[str, Any],
    baseline: dict[str, str],
    page_bundle: dict[str, Any] | None,
) -> dict[str, Any]:
    blocks_by_id = {
        str(block.get("block_id") or ""): block
        for block in canonical.get("blocks") or []
        if isinstance(block, dict)
    }
    sections = [
        {
            "section_id": str(section.get("section_id") or ""),
            "title": str(section.get("title") or ""),
            "level": int(section.get("level") or 1),
            "source_order": int(section.get("order") or 0),
            "source_start": int(section.get("source_start") or 0),
            "baseline_role": baseline.get(str(section.get("section_id") or ""), "other"),
            "excerpt": _section_excerpt(section, blocks_by_id),
        }
        for section in canonical.get("sections") or []
        if isinstance(section, dict) and str(section.get("section_id") or "")
    ]
    return {
        "metadata": {
            "board": str(metadata.get("board") or ""),
            "grade": str(metadata.get("grade") or ""),
            "subject": str(metadata.get("subject") or ""),
            "unit": str(metadata.get("unit") or ""),
            "chapter": str(metadata.get("chapter_title") or ""),
            "subject_adapter": subject_adapter(metadata.get("subject")),
        },
        "sections": sections,
        "verified_pdf_headings": _vision_heading_evidence(page_bundle),
        "allowed_roles": [
            "chapter_heading", "main_topic", "subtopic", "activity",
            "exercise", "source_extract", "glossary", "box",
            "content_heading", "other",
        ],
    }


def _hierarchy_response_schema(section_ids: list[str]) -> dict[str, Any]:
    return {
        "name": "phase3_semantic_hierarchy",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "sections": {
                    "type": "array",
                    "minItems": len(section_ids),
                    "maxItems": len(section_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "section_id": {"type": "string", "enum": section_ids},
                            "role": {
                                "type": "string",
                                "enum": [
                                    "chapter_heading", "main_topic", "subtopic",
                                    "activity", "exercise", "source_extract",
                                    "glossary", "box", "content_heading", "other",
                                ],
                            },
                            "parent_section_id": {
                                "type": "string",
                                "enum": ["", *section_ids],
                            },
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 4,
                            },
                        },
                        "required": [
                            "section_id", "role", "parent_section_id",
                            "confidence", "evidence",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["sections"],
            "additionalProperties": False,
        },
    }


def _hierarchy_critic_schema(section_ids: list[str]) -> dict[str, Any]:
    return {
        "name": "phase3_semantic_hierarchy_critic",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["verified", "repair_required"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "repairs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "section_id": {"type": "string", "enum": section_ids},
                            "role": {
                                "type": "string",
                                "enum": [
                                    "chapter_heading", "main_topic", "subtopic",
                                    "activity", "exercise", "source_extract",
                                    "glossary", "box", "content_heading", "other",
                                ],
                            },
                            "parent_section_id": {
                                "type": "string",
                                "enum": ["", *section_ids],
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["section_id", "role", "parent_section_id", "reason"],
                        "additionalProperties": False,
                    },
                },
                "issues": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            },
            "required": ["verdict", "confidence", "repairs", "issues"],
            "additionalProperties": False,
        },
    }


def _classify_hierarchy_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    section_ids = [str(row["section_id"]) for row in payload.get("sections") or []]
    system = (
        "You are the semantic source compiler for a universal textbook system. "
        "Classify every supplied section by its pedagogical document role. Use "
        "only the opaque section IDs supplied. Numbering, typography, extracted "
        "text, verified PDF headings, and nearby content are evidence, not an "
        "excuse to invent or reorder source material. Main topics are the actual "
        "teaching divisions of the chapter. Activity, Discuss, Source, glossary, "
        "boxes, review questions, and editorial labels are never main topics. "
        "Preserve the chapter's physical order. Return every section exactly once."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_hierarchy_response_schema(section_ids),
        purpose="concept_mapping",
        max_tokens=max(6000, min(24000, len(section_ids) * 260)),
    )


def _critic_hierarchy_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    section_ids = [str(row["section_id"]) for row in payload.get("sections") or []]
    system = (
        "Independently audit a proposed textbook hierarchy against the supplied "
        "source evidence. Detect activity/glossary/source labels promoted as main "
        "topics, lost numbered parents, subtopics attached to the wrong main "
        "topic, and any order drift. Use only supplied IDs and allowed roles. "
        "Return repairs only when source evidence supports them."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_hierarchy_critic_schema(section_ids),
        purpose="concept_mapping",
        max_tokens=max(4000, min(16000, len(section_ids) * 180)),
    )


def _validate_classifications(
    section_ids: list[str], value: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    rows = value.get("sections") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Phase 3 hierarchy classifier returned no sections array")
    by_id: dict[str, dict[str, Any]] = {}
    allowed = set(section_ids)
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Phase 3 hierarchy classifier returned a non-object row")
        section_id = str(row.get("section_id") or "")
        if section_id not in allowed or section_id in by_id:
            raise ValueError("Phase 3 hierarchy classifier changed section identity")
        parent = str(row.get("parent_section_id") or "")
        if parent and parent not in allowed:
            raise ValueError("Phase 3 hierarchy classifier invented a parent section")
        by_id[section_id] = copy.deepcopy(row)
    if set(by_id) != allowed:
        missing = sorted(allowed - set(by_id))
        raise ValueError(
            "Phase 3 hierarchy classifier omitted section IDs: " + ", ".join(missing)
        )
    return by_id


def _apply_critic_repairs(
    classifications: dict[str, dict[str, Any]], critic: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    verdict = str(critic.get("verdict") or "")
    confidence = float(critic.get("confidence") or 0.0)
    issues = [str(value).strip() for value in critic.get("issues") or [] if str(value).strip()]
    if not confidence_policy.accepts(confidence):
        raise ValueError(
            "Phase 3 hierarchy critic confidence "
            f"{confidence:.3f} is below "
            f"{confidence_policy.threshold_text()}"
        )
    if verdict == "verified" and not issues:
        return classifications
    repaired = copy.deepcopy(classifications)
    for row in critic.get("repairs") or []:
        if not isinstance(row, dict):
            continue
        section_id = str(row.get("section_id") or "")
        if section_id not in repaired:
            raise ValueError("Phase 3 hierarchy critic invented a section ID")
        repaired[section_id]["role"] = str(row.get("role") or repaired[section_id]["role"])
        repaired[section_id]["parent_section_id"] = str(row.get("parent_section_id") or "")
        repaired[section_id]["critic_reason"] = str(row.get("reason") or "")
    repairs = [row for row in critic.get("repairs") or [] if isinstance(row, dict)]
    if verdict == "repair_required" and not repairs:
        detail = "; ".join(issues[:10]) or "critic requested repair without a bounded repair"
        raise ValueError("Phase 3 hierarchy critic requires review: " + detail)
    if issues and not repairs:
        raise ValueError(
            "Phase 3 hierarchy critic requires review: " + "; ".join(issues[:10])
        )
    if verdict not in {"verified", "repair_required"}:
        raise ValueError(
            "Phase 3 hierarchy critic returned an unsupported verdict: " + verdict
        )
    return repaired


def _forced_structural_roles(
    canonical: dict[str, Any],
    classifications: dict[str, dict[str, Any]],
    *,
    numbered_hierarchy_active: bool = True,
    fallback_main_section_ids: set[str] | None = None,
    numbered_main_bindings: list[dict[str, Any]] | None = None,
) -> None:
    """Protect proven source hierarchy without mistaking chapter numbers for topics."""
    fallback_main_section_ids = set(fallback_main_section_ids or set())
    for section_id in fallback_main_section_ids:
        if section_id in classifications:
            classifications[section_id]["role"] = "main_topic"
            classifications[section_id]["parent_section_id"] = ""
    if not numbered_hierarchy_active:
        return
    mains, subs = structure.numbered_heading_inventory(canonical)
    main_section_by_number = {
        int(str(row.get("number") or 0)): str(
            row.get("resolved_section_id") or ""
        )
        for row in numbered_main_bindings or []
        if str(row.get("number") or "").isdigit()
    }
    if not main_section_by_number:
        main_section_by_number = {
            number: str(block.get("section_id") or "")
            for number, block in mains.items()
        }
    for number, section_id in main_section_by_number.items():
        if section_id in classifications:
            classifications[section_id]["role"] = "main_topic"
            classifications[section_id]["parent_section_id"] = ""
            classifications[section_id]["structural_number"] = str(number)
    for major, blocks in subs.items():
        parent = main_section_by_number.get(major, "")
        for block in blocks:
            section_id = str(block.get("section_id") or "")
            if section_id in classifications:
                classifications[section_id]["role"] = "subtopic"
                classifications[section_id]["parent_section_id"] = parent
                number, _title = _title_number(
                    (block.get("heading") or {}).get("title"), block.get("raw_text")
                )
                classifications[section_id]["structural_number"] = number


def compile_semantic_graph(
    canonical: dict[str, Any],
    *,
    source_text: str,
    metadata: dict[str, Any] | None = None,
    page_bundle: dict[str, Any] | None = None,
    hierarchy_provider: HierarchyProvider | None = None,
    critic_provider: HierarchyCritic | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile a stable graph from ACSD and optional API/PDF evidence."""
    metadata = dict(metadata or {})
    canonical = copy.deepcopy(canonical)
    chapter_title = str(
        metadata.get("chapter_title")
        or (canonical.get("document") or {}).get("chapter_title")
        or ""
    ).strip()
    sections = [
        copy.deepcopy(row) for row in canonical.get("sections") or []
        if isinstance(row, dict) and str(row.get("section_id") or "")
    ]
    sections.extend(_virtual_missing_main_candidates(canonical, page_bundle))
    numbered_main_bindings = _numbered_main_binding_rows(
        canonical,
        sections,
        materialize_projections=True,
    )
    sections.sort(key=lambda row: (
        int(row.get("source_start") or 0),
        int(row.get("order") or 0),
        str(row.get("section_id") or ""),
    ))

    mains, subs = structure.numbered_heading_inventory(canonical)
    numbered_hierarchy_active = bool(mains)
    if len(mains) == 1:
        only_number, only_block = next(iter(mains.items()))
        only_title = str((only_block.get("heading") or {}).get("title") or "").strip()
        # Math textbooks often emit a standalone chapter number ("5") and
        # then use 5.1/5.2 plus unnumbered teaching headings. That lone number
        # is a chapter marker, not a topic.
        if not re.search(r"[A-Za-z\u0080-\uffff]", only_title) or _normal(only_title) == str(only_number):
            numbered_hierarchy_active = False
    fallback_main_section_ids: set[str] = set()
    fallback_topic_titles: list[str] = []
    if not numbered_hierarchy_active or not mains:
        try:
            from . import generation as _generation
            fallback_topic_titles = _generation._topic_headings(
                _generation.parse_mmd_sections(source_text)
            )
        except Exception:
            fallback_topic_titles = []
        section_ids_by_title: dict[str, list[tuple[int, int, str]]] = {}
        for section in sections:
            section_ids_by_title.setdefault(_semantic_title_key(section.get("title")), []).append((
                int(section.get("source_start") or 0),
                int(section.get("level") or 1),
                str(section.get("section_id") or ""),
            ))
        for title in fallback_topic_titles:
            matches = [
                value for value in section_ids_by_title.get(_semantic_title_key(title), [])
                if value[2]
            ]
            if matches:
                # A chapter title can be repeated as a numbered teaching section.
                # Prefer the later/deeper occurrence rather than the cover heading.
                matches.sort(key=lambda value: (value[0], value[1]))
                fallback_main_section_ids.add(matches[-1][2])
    numbered_main_ids = (
        {
            str(row.get("resolved_section_id") or "")
            for row in numbered_main_bindings
            if str(row.get("resolved_section_id") or "")
        }
        if numbered_hierarchy_active else set()
    ) | fallback_main_section_ids
    numbered_sub_ids = (
        {
            str(block.get("section_id") or "")
            for rows in subs.values() for block in rows
            if str(block.get("section_id") or "")
        }
        if numbered_hierarchy_active else set()
    ) - fallback_main_section_ids
    for virtual in sections:
        if virtual.get("phase3_virtual"):
            numbered_main_ids.add(str(virtual.get("section_id") or ""))

    fallback_subtopic_parent: dict[str, str] = {}
    if fallback_main_section_ids:
        ordered_fallback_mains = sorted(
            [
                section for section in sections
                if str(section.get("section_id") or "") in fallback_main_section_ids
            ],
            key=lambda row: int(row.get("source_start") or 0),
        )
        for section in sections:
            sid = str(section.get("section_id") or "")
            if not sid or sid in fallback_main_section_ids:
                continue
            title = str(section.get("title") or "").strip()
            try:
                level = int(section.get("level") or 1)
            except (TypeError, ValueError):
                level = 1
            if level <= 1 or _AUXILIARY_TITLE_RE.match(title):
                continue
            position = int(section.get("source_start") or 0)
            parents = [
                row for row in ordered_fallback_mains
                if int(row.get("source_start") or 0) <= position
            ]
            if parents:
                fallback_subtopic_parent[sid] = str(parents[-1].get("section_id") or "")

    baseline = {
        str(section.get("section_id") or ""): _baseline_section_role(
            section,
            chapter_title=chapter_title,
            numbered_main_section_ids=numbered_main_ids,
            numbered_sub_section_ids=numbered_sub_ids,
            has_numbered_mains=(
                numbered_hierarchy_active or bool(fallback_main_section_ids)
            ),
        )
        for section in sections
    }
    for sid in fallback_subtopic_parent:
        if baseline.get(sid) == "content_heading":
            baseline[sid] = "subtopic"
    section_ids = list(baseline)
    classifications = {
        section_id: {
            "section_id": section_id,
            "role": role,
            "parent_section_id": "",
            "confidence": 1.0,
            "evidence": ["deterministic baseline"],
        }
        for section_id, role in baseline.items()
    }
    classification_mode = "deterministic"
    if hierarchy_provider is not None and section_ids:
        payload = _classification_payload(
            {**canonical, "sections": sections},
            metadata=metadata,
            baseline=baseline,
            page_bundle=page_bundle,
        )
        classifications = _validate_classifications(
            section_ids, hierarchy_provider(payload)
        )
        classification_mode = "api_classified"
        if critic_provider is not None:
            critic_payload = {
                **payload,
                "proposed_hierarchy": list(classifications.values()),
            }
            classifications = _apply_critic_repairs(
                classifications, critic_provider(critic_payload)
            )
            classification_mode = "api_classified_and_verified"

    # The source numbering contract outranks semantic model drift.
    _forced_structural_roles(
        canonical,
        classifications,
        numbered_hierarchy_active=numbered_hierarchy_active,
        # Unnumbered parser results are candidates, not semantic authority. They
        # remain deterministic fallbacks only when no hierarchy API was used.
        fallback_main_section_ids=(
            fallback_main_section_ids if hierarchy_provider is None else set()
        ),
        numbered_main_bindings=numbered_main_bindings,
    )
    if hierarchy_provider is None:
        for sid, parent_sid in fallback_subtopic_parent.items():
            if sid in classifications:
                classifications[sid]["role"] = "subtopic"
                classifications[sid]["parent_section_id"] = parent_sid
                classifications[sid]["evidence"] = [
                    "publisher-neutral fallback heading hierarchy"
                ]
    for section in sections:
        if section.get("phase3_virtual"):
            sid = str(section.get("section_id") or "")
            classifications[sid] = {
                "section_id": sid,
                "role": "main_topic",
                "parent_section_id": "",
                "confidence": 1.0,
                "evidence": ["verified PDF heading repairs missing numbered parent"],
                "structural_number": str(section.get("number") or ""),
            }

    # Build stable main-topic identities in physical order.
    main_sections = [
        section for section in sections
        if classifications[str(section.get("section_id") or "")]["role"] == "main_topic"
    ]
    if not main_sections:
        # Headingless chapters still require one stable topology node. The
        # selected chapter is authoritative; no model-authored title is used.
        first_start = min(
            [int(row.get("source_start") or 0) for row in sections] or [0]
        )
        main_sections = [{
            "section_id": "PHASE3-SYNTHETIC-TOPIC",
            "title": chapter_title or "Chapter Content",
            "source_start": first_start,
            "source_end": len(str(source_text or "")),
            "order": 1,
            "phase3_synthetic": True,
        }]
        classifications["PHASE3-SYNTHETIC-TOPIC"] = {
            "section_id": "PHASE3-SYNTHETIC-TOPIC",
            "role": "main_topic",
            "parent_section_id": "",
            "confidence": 1.0,
            "evidence": ["headingless chapter fallback"],
        }
    main_sections.sort(key=lambda row: (
        int(row.get("source_start") or 0), str(row.get("section_id") or "")
    ))

    topics: list[dict[str, Any]] = []
    topic_by_section: dict[str, dict[str, Any]] = {}
    numbered_binding_by_section = {
        str(row.get("resolved_section_id") or ""): row
        for row in numbered_main_bindings
        if str(row.get("resolved_section_id") or "")
    }
    for index, section in enumerate(main_sections, start=1):
        section_id = str(section.get("section_id") or "")
        number, title = _title_number(section.get("title"), section.get("raw_text"))
        numbered_binding = numbered_binding_by_section.get(section_id)
        if isinstance(numbered_binding, dict):
            number = str(numbered_binding.get("number") or number)
            title = str(numbered_binding.get("expected_title") or title)
        title = _plain_title(title or section.get("title")) or f"Topic {index}"
        topic = {
            "topic_id": f"TOPIC-{index:04d}",
            "order": index,
            "title": title,
            "display_title": title,
            "structural_number": number,
            "section_id": section_id,
            "source_start": int(section.get("source_start") or 0),
            "source_end": int(section.get("source_end") or 0),
            "source": (
                "verified_pdf"
                if section.get("phase3_virtual")
                else (
                    "canonical_heading_projection"
                    if section.get("phase3_numbered_projection")
                    else "acsd"
                )
            ),
        }
        topics.append(topic)
        topic_by_section[section_id] = topic
    for index, topic in enumerate(topics):
        topic["source_end"] = (
            topics[index + 1]["source_start"] if index + 1 < len(topics)
            else len(str(source_text or ""))
        )

    def topic_for_position(position: int) -> dict[str, Any]:
        prior = [row for row in topics if int(row["source_start"]) <= position]
        return prior[-1] if prior else topics[0]

    # Build stable subtopic nodes. Numbered ancestry is authoritative; API may
    # also identify unnumbered pedagogical subtopics.
    subtopic_sections = [
        section for section in sections
        if classifications[str(section.get("section_id") or "")]["role"] == "subtopic"
    ]
    subtopic_sections.sort(key=lambda row: (
        int(row.get("source_start") or 0), str(row.get("section_id") or "")
    ))
    subtopics: list[dict[str, Any]] = []
    subtopic_by_section: dict[str, dict[str, Any]] = {}
    counts_by_topic: dict[str, int] = {}
    for section in subtopic_sections:
        sid = str(section.get("section_id") or "")
        parent_sid = str(classifications[sid].get("parent_section_id") or "")
        parent_topic = topic_by_section.get(parent_sid)
        if parent_topic is None:
            parent_topic = topic_for_position(int(section.get("source_start") or 0))
        counts_by_topic[parent_topic["topic_id"]] = counts_by_topic.get(
            parent_topic["topic_id"], 0
        ) + 1
        local_index = counts_by_topic[parent_topic["topic_id"]]
        number, title = _title_number(section.get("title"), section.get("raw_text"))
        title = _plain_title(title or section.get("title"))
        node = {
            "subtopic_id": f"{parent_topic['topic_id']}-SUB-{local_index:03d}",
            "order": len(subtopics) + 1,
            "topic_id": parent_topic["topic_id"],
            "title": title,
            "display_title": title,
            "structural_number": number,
            "section_id": sid,
            "source_start": int(section.get("source_start") or 0),
            "source_end": int(section.get("source_end") or 0),
        }
        subtopics.append(node)
        subtopic_by_section[sid] = node
    for topic in topics:
        children = [row for row in subtopics if row["topic_id"] == topic["topic_id"]]
        for index, child in enumerate(children):
            child["source_end"] = (
                children[index + 1]["source_start"] if index + 1 < len(children)
                else topic["source_end"]
            )

    def subtopic_for_position(topic_id: str, position: int) -> dict[str, Any] | None:
        prior = [
            row for row in subtopics
            if row["topic_id"] == topic_id and int(row["source_start"]) <= position
        ]
        return prior[-1] if prior else None

    sections_out: list[dict[str, Any]] = []
    for section in sections:
        sid = str(section.get("section_id") or "")
        position = int(section.get("source_start") or 0)
        topic = topic_by_section.get(sid) or topic_for_position(position)
        subtopic = subtopic_by_section.get(sid) or subtopic_for_position(
            topic["topic_id"], position
        )
        role = classifications[sid]["role"]
        if role == "main_topic":
            subtopic = None
        sections_out.append({
            "section_id": sid,
            "order": int(section.get("order") or 0),
            "source_start": position,
            "source_end": int(section.get("source_end") or position),
            "title": str(section.get("title") or ""),
            "role": role,
            "topic_id": topic["topic_id"],
            "subtopic_id": subtopic["subtopic_id"] if subtopic else "",
            "parent_section_id": str(classifications[sid].get("parent_section_id") or ""),
            "confidence": float(classifications[sid].get("confidence") or 0.0),
            "evidence": list(classifications[sid].get("evidence") or []),
            "phase3_virtual": bool(section.get("phase3_virtual")),
            "phase3_numbered_projection": bool(
                section.get("phase3_numbered_projection")
            ),
        })

    section_graph_by_id = {row["section_id"]: row for row in sections_out}
    numbered_binding_by_block = {
        str(row.get("block_id") or ""): row
        for row in numbered_main_bindings
        if str(row.get("block_id") or "")
    }
    blocks: list[dict[str, Any]] = []
    suspicious_blocks: list[str] = []
    for block in sorted(
        [row for row in canonical.get("blocks") or [] if isinstance(row, dict)],
        key=lambda row: int(row.get("order") or 0),
    ):
        block_id = str(block.get("block_id") or "")
        kind = str(block.get("kind") or "")
        binding = numbered_binding_by_block.get(block_id)
        sid = str(
            (binding or {}).get("resolved_section_id")
            or block.get("section_id")
            or ""
        )
        section_node = section_graph_by_id.get(sid)
        position = int(block.get("source_start") or 0)
        topic = (
            next((row for row in topics if row["topic_id"] == section_node["topic_id"]), None)
            if section_node else topic_for_position(position)
        ) or topics[0]
        subtopic = (
            next((row for row in subtopics if row["subtopic_id"] == section_node["subtopic_id"]), None)
            if section_node and section_node.get("subtopic_id") else
            subtopic_for_position(topic["topic_id"], position)
        )
        raw_text = str(block.get("raw_text") or "")
        if _SUSPICIOUS_MARKUP_RE.search(raw_text):
            suspicious_blocks.append(str(block.get("block_id") or ""))
        blocks.append({
            "block_id": block_id,
            "order": int(block.get("order") or 0),
            "kind": kind or "other",
            "section_id": sid,
            "topic_id": topic["topic_id"],
            "subtopic_id": subtopic["subtopic_id"] if subtopic else "",
            "source_start": position,
            "source_end": int(block.get("source_end") or position),
            "raw_sha256": str(block.get("raw_sha256") or _sha256_text(raw_text)),
            "figure_id": str(block.get("figure_id") or ""),
            "image_ids": list(block.get("image_ids") or []),
            "math_ids": list(block.get("math_ids") or []),
            "task_ids": list(block.get("task_ids") or []),
        })

    tasks: list[dict[str, Any]] = []
    for task in sorted(
        [row for row in canonical.get("tasks") or [] if isinstance(row, dict)],
        key=lambda row: (int(row.get("order") or 0), int(row.get("source_start") or 0)),
    ):
        position = int(task.get("source_start") or 0)
        topic = topic_for_position(position)
        subtopic = subtopic_for_position(topic["topic_id"], position)
        tasks.append({
            "task_id": str(task.get("task_id") or ""),
            "qid": str(task.get("qid") or ""),
            "order": int(task.get("order") or 0),
            "topic_id": topic["topic_id"],
            "subtopic_id": subtopic["subtopic_id"] if subtopic else "",
            "section_id": str(task.get("section_id") or ""),
            "source_start": position,
            "source_end": int(task.get("source_end") or position),
            "source_label": str(task.get("source_label") or ""),
            "source_kind": str(task.get("source_kind") or ""),
            "identity_key": str(task.get("identity_key") or ""),
            "display_prompt": str(task.get("display_prompt") or ""),
            "figure_ids": [str(value) for value in task.get("figure_refs") or []],
            "image_urls": [str(value) for value in task.get("image_urls") or []],
            "requires_visual": bool(task.get("requires_visual")),
            "chapter_wide": bool(task.get("chapter_wide") or task.get("_topic_scope") == "chapter"),
        })

    graph = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "semantic_confidence_policy": confidence_policy.cache_identity(),
        "phase": PHASE,
        "status": "ready" if not suspicious_blocks else "review_required",
        "source_contract_hash": source_contract_hash(canonical),
        "semantic_context_hash": semantic_context_hash(metadata),
        "source_sha256": str(
            (canonical.get("document") or {}).get("source_sha256") or _sha256_text(source_text)
        ),
        "semantic_source_sha256": "",
        "classification_mode": classification_mode,
        "numbered_main_bindings": copy.deepcopy(numbered_main_bindings),
        "metadata": {
            **metadata,
            "subject_adapter": subject_adapter(metadata.get("subject")),
        },
        "topics": topics,
        "subtopics": subtopics,
        "sections": sections_out,
        "blocks": blocks,
        "tasks": tasks,
        "figures": copy.deepcopy(canonical.get("figures") or []),
        "images": copy.deepcopy(canonical.get("images") or []),
        "math": copy.deepcopy(canonical.get("math") or []),
        "vision_evidence": {
            "available": bool(page_bundle),
            "schema_version": str((page_bundle or {}).get("schema_version") or ""),
            "pdf_sha256": str((page_bundle or {}).get("pdf_sha256") or ""),
            "page_count": len((page_bundle or {}).get("pages") or []),
            "heading_count": len(_vision_heading_evidence(page_bundle)),
        },
        "issues": [
            {
                "code": "converter_semantic_markup_requires_pdf_reconciliation",
                "severity": "error",
                "block_ids": suspicious_blocks,
                "message": (
                    "Converter-specific semantic markup was found in source blocks; "
                    "the original PDF must verify or replace those blocks before "
                    "semantic generation."
                ),
            }
        ] if suspicious_blocks else [],
    }
    semantic_source = render_semantic_source(graph, canonical)
    graph["semantic_source_sha256"] = _sha256_text(semantic_source)
    errors = validate_graph(
        graph,
        canonical=canonical,
        semantic_source=semantic_source,
        allow_unresolved_source_anomalies=bool(suspicious_blocks),
    )
    if errors:
        graph["status"] = "failed"
        graph["issues"].extend(errors)
    report = {
        "schema_name": f"{SCHEMA_NAME} Report",
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "semantic_confidence_policy": confidence_policy.cache_identity(),
        "phase": PHASE,
        "status": graph["status"],
        "source_contract_hash": graph["source_contract_hash"],
        "semantic_context_hash": graph["semantic_context_hash"],
        "source_sha256": graph["source_sha256"],
        "semantic_source_sha256": graph["semantic_source_sha256"],
        "classification_mode": classification_mode,
        "summary": {
            "topics": len(topics),
            "subtopics": len(subtopics),
            "sections": len(sections_out),
            "blocks": len(blocks),
            "tasks": len(tasks),
            "figures": len(graph["figures"]),
            "images": len(graph["images"]),
            "math_spans": len(graph["math"]),
            "vision_pages": int(graph["vision_evidence"]["page_count"]),
            "errors": sum(1 for row in graph["issues"] if row.get("severity") == "error"),
            "warnings": sum(1 for row in graph["issues"] if row.get("severity") == "warning"),
        },
        "issues": copy.deepcopy(graph["issues"]),
    }
    return graph, report


def _clean_list(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\\begin\{(?:itemize|enumerate)\}", "\n", text, flags=re.I)
    text = re.sub(r"\\end\{(?:itemize|enumerate)\}", "\n", text, flags=re.I)
    text = _LIST_ITEM_RE.sub(
        lambda match: "\n- " + ((match.group("label") or "").strip() + " " if match.group("label") else ""),
        text,
    )
    return text


def _clean_table(value: str) -> str:
    text = str(value or "")
    # Preserve the learner-visible cell content of common publisher layout
    # macros while discarding only their row/column spanning instructions.
    text = re.sub(
        r"\\multirow(?:\[[^\]]*\])?\{[^{}]*\}\{[^{}]*\}\{([^{}]*)\}",
        r"\1",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\\multicolumn\{[^{}]*\}\{[^{}]*\}\{([^{}]*)\}",
        r"\1",
        text,
        flags=re.I,
    )
    text = re.sub(r"\\cline\{[^{}]*\}", "\n", text, flags=re.I)
    text = structure.normalize_task_table_markup(text)
    text = _TABLE_BEGIN_RE.sub("\n", text)
    return text


def _protect_rich_tokens(value: str) -> tuple[str, list[str]]:
    """Protect URLs and canonical rich-text tokens from table normalization."""
    text = str(value or "")
    protected: list[str] = []

    def stash(rendered: str) -> str:
        token = f"@@AEGIS_PHASE3_TOKEN_{len(protected):05d}@@"
        protected.append(rendered)
        return token

    text = _CANONICAL_KATEX_ANY_RE.sub(lambda match: stash(match.group(0)), text)
    text = _CANONICAL_IMAGE_ANY_RE.sub(lambda match: stash(match.group(0)), text)

    def markdown_image(match: re.Match) -> str:
        src = str(match.group("src") or "").replace(r"\&", "&")
        alt = str(match.group("alt") or "").strip() or "Source visual"
        try:
            rendered = kr.image(src, alt)
        except ValueError:
            rendered = match.group(0).replace(r"\&", "&")
        return stash(rendered)

    text = _MARKDOWN_IMAGE_ANY_RE.sub(markdown_image, text)

    def includegraphics(match: re.Match) -> str:
        src = str(match.group("src") or "").replace(r"\&", "&")
        try:
            rendered = kr.image(src, "Source visual")
        except ValueError:
            rendered = match.group(0).replace(r"\&", "&")
        return stash(rendered)

    text = _INCLUDEGRAPHICS_ANY_RE.sub(includegraphics, text)

    def bare_url(match: re.Match) -> str:
        url = match.group(0).replace(r"\&", "&")
        return stash(url)

    text = _BARE_HTTPS_RE.sub(bare_url, text)
    return text, protected


def _restore_rich_tokens(value: str, protected: list[str]) -> str:
    text = str(value or "")
    for index, rendered in enumerate(protected):
        text = text.replace(f"@@AEGIS_PHASE3_TOKEN_{index:05d}@@", rendered)
    return text


def _machine_metadata_comment(body: object) -> bool:
    """Whether one HTML comment is Aegis-authored, non-visible metadata."""

    value = _SPACE_RE.sub(" ", str(body or "")).strip()
    if value.casefold() == "aegis canonical source shadow":
        return True
    key_match = re.match(r"(?P<key>[a-z][a-z0-9_-]*)\s*:", value, re.I)
    if (
        key_match
        and str(key_match.group("key") or "").casefold()
        in _MACHINE_METADATA_KEYS
    ):
        return True
    if re.match(
        r"invalid-image-url preserved in canonical json\s*:",
        value,
        re.I,
    ):
        return True
    return bool(re.fullmatch(
        r"BLK-\d+\s*:\s*source whitespace preserved in canonical JSON",
        value,
        re.I,
    ))


def _strip_machine_metadata_comments(value: object) -> str:
    """Remove only known Aegis metadata comments outside literal code.

    HTML comments can be legitimate Computer Science source examples. Fenced
    and inline Markdown code therefore remain byte-for-byte protected, and an
    unknown comment remains visible to the strict rich-text validator.
    """

    def strip_comments(text: str) -> str:
        return _HTML_COMMENT_RE.sub(
            lambda match: " " if _machine_metadata_comment(
                match.group("body")
            ) else match.group(0),
            text,
        )

    return kr.transform_outside_markdown_code(
        str(value or ""),
        strip_comments,
    )


def _protect_markdown_code(value: str) -> tuple[str, list[tuple[str, str]]]:
    """Stash Markdown code through Phase 3 prose normalization."""

    text = str(value or "")
    ranges = kr._markdown_code_ranges(text)
    if not ranges:
        return text, []
    prefix = "\ue100AEGISPHASE3CODE"
    while prefix in text:
        prefix += "\ue100"
    protected: list[tuple[str, str]] = []
    pieces: list[str] = []
    cursor = 0
    for index, (start, end) in enumerate(ranges):
        token = f"{prefix}{index:05d}\ue101"
        pieces.extend((text[cursor:start], token))
        protected.append((token, text[start:end]))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), protected


def _restore_markdown_code(
    value: str,
    protected: list[tuple[str, str]],
) -> str:
    text = str(value or "")
    for token, rendered in protected:
        text = text.replace(token, rendered)
    return text


def _clean_public_text(
    value: object,
    *,
    strip_machine_metadata: bool = True,
    preserve_markdown_code: bool = True,
) -> str:
    text = str(value or "")
    if strip_machine_metadata:
        text = _strip_machine_metadata_comments(text)
    code_protected: list[tuple[str, str]] = []
    if preserve_markdown_code:
        text, code_protected = _protect_markdown_code(text)
    text, protected = _protect_rich_tokens(text)
    text = _clean_list(_clean_table(text))
    text = _LAYOUT_COMMAND_RE.sub(" ", text)
    text = _ENV_RE.sub("\n", text)
    text = re.sub(r"\\(?:section|subsection|subsubsection|chapter)\*?\{([^{}]*)\}", r"\1", text)
    text = _restore_rich_tokens(text, protected)
    for pattern in _EMPTY_MATH_DELIMITER_PATTERNS:
        text = pattern.sub(" ", text)
    text = kr.canonicalize_rich_text(text)
    issues = set(kr.rich_text_issues(text))
    if issues and issues.issubset({"raw_latex", "raw_math_expression"}):
        repaired = kr.repair_unwrapped_math(text)
        if not kr.rich_text_issues(repaired):
            text = repaired
    # Any residual layout/control command is not learner-visible source text.
    text = re.sub(r"\\(?:hline|vspace|hspace|noindent|smallskip|medskip|bigskip)\b", " ", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line).strip()
    return _restore_markdown_code(text, code_protected)


def _graph_block_text(
    graph_block: dict[str, Any] | None,
    canonical_block: dict[str, Any] | None,
) -> str:
    override = (graph_block or {}).get("source_override")
    if isinstance(override, dict) and str(override.get("resolved_text") or ""):
        resolved = str(override.get("resolved_text") or "")
        if override.get("resolved_sha256") == _sha256_text(resolved):
            return resolved
    source = canonical_block or {}
    return str(source.get("display_text") or source.get("raw_text") or "")


def render_semantic_source(
    graph: dict[str, Any],
    canonical: dict[str, Any],
    *,
    strip_machine_metadata: bool = True,
    preserve_markdown_code: bool = True,
) -> str:
    """Render graph-controlled source text without model-authored wrappers.

    Consecutive source blocks are normalized as one stream. Mathpix may split a
    display-math opener, equation body, and closer into three ACSD blocks; a
    block-by-block renderer would preserve orphan ``\\[``/``\\]`` delimiters.
    """
    graph_sections = {
        str(row.get("section_id") or ""): row
        for row in graph.get("sections") or [] if isinstance(row, dict)
    }
    topic_by_id = {
        str(row.get("topic_id") or ""): row
        for row in graph.get("topics") or [] if isinstance(row, dict)
    }
    subtopic_by_id = {
        str(row.get("subtopic_id") or ""): row
        for row in graph.get("subtopics") or [] if isinstance(row, dict)
    }
    figure_by_id = {
        str(row.get("figure_id") or ""): row
        for row in canonical.get("figures") or [] if isinstance(row, dict)
    }
    image_by_id = {
        str(row.get("image_id") or ""): row
        for row in canonical.get("images") or [] if isinstance(row, dict)
    }
    graph_block_by_id = {
        str(row.get("block_id") or ""): row
        for row in graph.get("blocks") or [] if isinstance(row, dict)
    }
    emitted_sections: set[str] = set()
    pieces: list[str] = []
    pending_text: list[str] = []
    virtual_sections = sorted(
        [
            row for row in graph.get("sections") or []
            if isinstance(row, dict) and row.get("phase3_virtual")
        ],
        key=lambda row: (
            int(row.get("source_start") or 0),
            str(row.get("section_id") or ""),
        ),
    )
    virtual_index = 0

    def flush_pending() -> None:
        if not pending_text:
            return
        cleaned = _clean_public_text(
            "\n".join(pending_text),
            strip_machine_metadata=strip_machine_metadata,
            preserve_markdown_code=preserve_markdown_code,
        )
        pending_text.clear()
        if cleaned:
            pieces.append(cleaned)

    def emit_virtual_sections_through(position: int) -> None:
        nonlocal virtual_index
        while virtual_index < len(virtual_sections):
            section = virtual_sections[virtual_index]
            if int(section.get("source_start") or 0) > position:
                break
            virtual_index += 1
            sid = str(section.get("section_id") or "")
            if not sid or sid in emitted_sections:
                continue
            flush_pending()
            emitted_sections.add(sid)
            topic = topic_by_id.get(str(section.get("topic_id") or ""), {})
            title = _plain_title(
                topic.get("title") or section.get("title") or "Topic",
                strip_machine_metadata=strip_machine_metadata,
            )
            pieces.append(f"## {title}")

    for block in sorted(
        [row for row in canonical.get("blocks") or [] if isinstance(row, dict)],
        key=lambda row: int(row.get("order") or 0),
    ):
        emit_virtual_sections_through(int(block.get("source_start") or 0))
        graph_block = graph_block_by_id.get(str(block.get("block_id") or ""))
        sid = str(
            (graph_block or {}).get("section_id")
            or block.get("section_id")
            or ""
        )
        section = graph_sections.get(sid)
        kind = str(block.get("kind") or "")
        if kind == "layout":
            continue
        if kind == "heading" and section:
            flush_pending()
            if sid in emitted_sections:
                continue
            emitted_sections.add(sid)
            role = str(section.get("role") or "content_heading")
            title = _plain_title(
                section.get("title"),
                strip_machine_metadata=strip_machine_metadata,
            )
            if role == "main_topic":
                title = _plain_title(
                    topic_by_id.get(str(section.get("topic_id") or ""), {}).get("title")
                    or title,
                    strip_machine_metadata=strip_machine_metadata,
                )
                pieces.append(f"## {title}")
            elif role == "subtopic":
                title = _plain_title(
                    subtopic_by_id.get(str(section.get("subtopic_id") or ""), {}).get("title")
                    or title,
                    strip_machine_metadata=strip_machine_metadata,
                )
                pieces.append(f"### {title}")
            elif role == "chapter_heading":
                pieces.append(f"# {title}")
            elif title and role not in {"other"}:
                pieces.append(f"### {title}")
            continue
        if kind == "figure":
            flush_pending()
            figure = figure_by_id.get(str(block.get("figure_id") or ""), {})
            caption = _clean_public_text(
                figure.get("caption_raw") or "Source visual",
                strip_machine_metadata=strip_machine_metadata,
                preserve_markdown_code=preserve_markdown_code,
            )
            tags: list[str] = []
            for image_id in figure.get("image_ids") or block.get("image_ids") or []:
                image = image_by_id.get(str(image_id), {})
                url = str(image.get("url") or "").strip().replace(r"\&", "&")
                if not url:
                    continue
                try:
                    tags.append(kr.image(url, caption or str(image.get("alt_raw") or "Source visual")))
                except ValueError:
                    continue
            if tags:
                pieces.append("\n".join(tags))
            if caption:
                pieces.append(caption)
            continue
        pending_text.append(_graph_block_text(
            graph_block, block
        ))
    flush_pending()
    emit_virtual_sections_through(10**18)
    rendered_pieces = [
        (
            piece.strip("\r\n")
            if preserve_markdown_code
            else piece.strip()
        )
        for piece in pieces
        if piece.strip()
    ]
    return "\n\n".join(rendered_pieces).strip("\r\n") + "\n"


def _source_tokens(value: object) -> set[str]:
    text = _SUSPICIOUS_MARKUP_RE.sub(" ", str(value or ""))
    text = re.sub(r"https://\S+", " ", text)
    text = re.sub(r"\\[A-Za-z]+(?:\[[^\]]*\])?", " ", text)
    return {token.casefold() for token in _WORD_TOKEN_RE.findall(text)}


def _page_block_visible_text(block: dict[str, Any]) -> str:
    parts = [
        str(block.get("text") or ""),
        str(block.get("caption") or ""),
        str(block.get("latex") or ""),
    ]
    parts.extend(
        str(cell or "")
        for row in block.get("table_rows") or []
        for cell in row
    )
    return " ".join(part for part in parts if part).strip()


def _page_block_key(page_id: object, reading_order: object) -> str:
    return f"{str(page_id or '')}:{int(reading_order or 0):04d}"


def _candidate_anomaly_packet(
    canonical_block: dict[str, Any],
    *,
    page_bundle: dict[str, Any],
    source_chars: int,
    limit: int = 18,
) -> dict[str, Any]:
    source_text = str(
        canonical_block.get("display_text") or canonical_block.get("raw_text") or ""
    )
    source_tokens = _source_tokens(source_text)
    page_count = max(1, len(page_bundle.get("pages") or []))
    predicted_page = min(
        page_count,
        max(
            1,
            int(
                (int(canonical_block.get("source_start") or 0) / max(1, source_chars))
                * page_count
            )
            + 1,
        ),
    )
    scored: list[tuple[float, int, int, dict[str, Any], dict[str, Any]]] = []
    canonical_kind = str(canonical_block.get("kind") or "")
    for page in page_bundle.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("page_number") or 0)
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            visible = _page_block_visible_text(block)
            tokens = _source_tokens(visible)
            overlap = len(source_tokens & tokens)
            coefficient = overlap / max(1, min(len(source_tokens), len(tokens)))
            kind_bonus = 0.20 if str(block.get("kind") or "") == canonical_kind else 0.0
            proximity = max(0.0, 0.10 - abs(page_number - predicted_page) * 0.025)
            score = coefficient + kind_bonus + proximity
            scored.append((
                score,
                page_number,
                int(block.get("reading_order") or 0),
                page,
                block,
            ))
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))
    chosen = scored[:limit]
    # Always retain table candidates close to the predicted page because a
    # converter anomaly is frequently a visual table cell with little OCR text.
    for row in scored:
        if len(chosen) >= limit + 6:
            break
        if row in chosen:
            continue
        score, page_number, _order, _page, block = row
        if (
            str(block.get("kind") or "") == "table"
            and abs(page_number - predicted_page) <= 2
        ):
            chosen.append(row)
    candidate_blocks: list[dict[str, Any]] = []
    candidate_page_numbers: list[int] = []
    seen: set[str] = set()
    for score, page_number, order, page, block in chosen:
        key = _page_block_key(page.get("page_id"), order)
        if key in seen:
            continue
        seen.add(key)
        if page_number not in candidate_page_numbers:
            candidate_page_numbers.append(page_number)
        candidate_blocks.append({
            "block_key": key,
            "page_id": str(page.get("page_id") or ""),
            "page_number": page_number,
            "reading_order": order,
            "kind": str(block.get("kind") or ""),
            "text": str(block.get("text") or ""),
            "latex": str(block.get("latex") or ""),
            "table_rows": copy.deepcopy(block.get("table_rows") or []),
            "linked_visual_orders": [
                int(value) for value in block.get("linked_visual_orders") or []
            ],
            "caption": str(block.get("caption") or ""),
            "confidence": float(block.get("confidence") or 0.0),
            "retrieval_score": round(float(score), 6),
        })
    return {
        "issue_code": "converter_semantic_markup_requires_pdf_reconciliation",
        "canonical_block": {
            "block_id": str(canonical_block.get("block_id") or ""),
            "kind": canonical_kind,
            "source_start": int(canonical_block.get("source_start") or 0),
            "source_end": int(canonical_block.get("source_end") or 0),
            "raw_text": source_text[:10000],
        },
        "candidate_blocks": candidate_blocks,
        "candidate_page_numbers": sorted(candidate_page_numbers),
        "instruction": (
            "Select the one already-verified original-PDF page block that "
            "contains the visible source evidence needed to replace the "
            "converter-only semantic markup. Do not write replacement text."
        ),
    }


def _anomaly_selection_schema(block_keys: list[str]) -> dict[str, Any]:
    allowed = ["NONE", *block_keys]
    return {
        "name": "aegis_phase3_source_anomaly_selection",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["use_verified_page_block", "review_required"],
                },
                "selected_block_key": {"type": "string", "enum": allowed},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
            },
            "required": [
                "decision", "selected_block_key", "confidence", "reason"
            ],
            "additionalProperties": False,
        },
    }


def _anomaly_critic_schema(block_keys: list[str]) -> dict[str, Any]:
    allowed = ["NONE", *block_keys]
    return {
        "name": "aegis_phase3_source_anomaly_verification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["verified", "rejected"]},
                "selected_block_key": {"type": "string", "enum": allowed},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "verdict", "selected_block_key", "confidence", "issues"
            ],
            "additionalProperties": False,
        },
    }


def _anomaly_evidence_pages(
    source_path: Path,
    page_numbers: Iterable[int],
) -> list[phase22.EvidencePage]:
    pages: list[phase22.EvidencePage] = []
    for number in sorted({int(value) for value in page_numbers if int(value) > 0}):
        row = page_acsd.collect_pdf_pages(
            source_path, start_page=number, end_page=number
        )[0]
        pages.append(phase22.EvidencePage(
            evidence_id=row.page_id,
            page_number=row.page_number,
            text=row.text,
            image_data_url=row.image_data_url,
            score=1.0,
        ))
    return pages


def _select_source_anomaly_via_openai(
    packet: dict[str, Any], *, source_path: Path
) -> dict[str, Any]:
    keys = [
        str(row.get("block_key") or "")
        for row in packet.get("candidate_blocks") or []
        if str(row.get("block_key") or "")
    ]
    system = (
        "You are the Aegis source-fusion selector. Compare the suspicious "
        "Mathpix/ACSD block with the supplied original-PDF pages and the "
        "already independently verified page-block ledger. Select only one "
        "opaque block_key from the ledger. Never transcribe, repair, or invent "
        "text, LaTeX, table cells, captions, or URLs. Return review_required "
        "unless the selected block visibly contains the exact source evidence."
    )
    prompt = json.dumps({
        key: value for key, value in packet.items()
        if key != "candidate_page_numbers"
    }, ensure_ascii=False, indent=2)
    return phase22._openai_multimodal_json(
        system=system,
        prompt=prompt,
        pages=_anomaly_evidence_pages(
            source_path, packet.get("candidate_page_numbers") or []
        ),
        response_schema=_anomaly_selection_schema(keys),
        purpose="source_adjudication",
        max_tokens=5000,
    )


def _critic_source_anomaly_via_openai(
    packet: dict[str, Any], selection: dict[str, Any], *, source_path: Path
) -> dict[str, Any]:
    keys = [
        str(row.get("block_key") or "")
        for row in packet.get("candidate_blocks") or []
        if str(row.get("block_key") or "")
    ]
    system = (
        "You are the independent Aegis source-fusion verifier. Verify that the "
        "selected opaque page block is visibly supported by the original PDF "
        "and is the correct evidence for the suspicious converter markup. Do "
        "not rewrite anything. Reject uncertain, incomplete, or merely nearby "
        "selections."
    )
    prompt = json.dumps({
        "packet": {
            key: value for key, value in packet.items()
            if key != "candidate_page_numbers"
        },
        "selection": selection,
    }, ensure_ascii=False, indent=2)
    return phase22._openai_multimodal_json(
        system=system,
        prompt=prompt,
        pages=_anomaly_evidence_pages(
            source_path, packet.get("candidate_page_numbers") or []
        ),
        response_schema=_anomaly_critic_schema(keys),
        purpose="source_adjudication",
        max_tokens=4000,
    )


def _page_and_block_by_key(
    page_bundle: dict[str, Any], block_key: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for page in page_bundle.get("pages") or []:
        if not isinstance(page, dict):
            continue
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            if _page_block_key(
                page.get("page_id"), block.get("reading_order")
            ) == block_key:
                return page, block
    return None, None


def _render_page_visual_marker(page: dict[str, Any], order: int) -> str:
    figure = next(
        (
            row for row in page.get("blocks") or []
            if isinstance(row, dict)
            and row.get("kind") == "figure"
            and int(row.get("reading_order") or 0) == int(order)
        ),
        None,
    )
    if not isinstance(figure, dict):
        raise ValueError("verified page table references an unknown visual block")
    url = str(figure.get("asset_url") or "").strip()
    if not url:
        raise ValueError("verified page visual was not materialized as a source asset")
    caption = str(figure.get("caption") or "Source table-cell visual").strip()
    return kr.image(url, caption or "Source table-cell visual")


def _render_page_cell(value: object, page: dict[str, Any]) -> str:
    text = str(value or "")
    return _VISUAL_MARKER_RE.sub(
        lambda match: _render_page_visual_marker(page, int(match.group(1))),
        text,
    )


def _canonical_table_rows(value: object) -> list[list[str]]:
    protected_text, protected = _protect_rich_tokens(str(value or ""))
    normalized = _clean_table(protected_text)
    normalized = _restore_rich_tokens(normalized, protected)
    rows: list[list[str]] = []
    for line in normalized.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append([cell.strip() for cell in line.split(" | ")])
    return rows


def _row_overlap(left: list[str], right: list[str], *, skip: int) -> float:
    left_tokens = _source_tokens(" ".join(
        cell for index, cell in enumerate(left) if index != skip
    ))
    right_tokens = _source_tokens(" ".join(
        cell for index, cell in enumerate(right) if index != skip
    ))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))


def _patch_suspicious_table(
    canonical_text: str,
    *,
    selected_block: dict[str, Any],
    selected_page: dict[str, Any],
) -> str:
    canonical_rows = _canonical_table_rows(canonical_text)
    page_rows = [
        [str(cell or "") for cell in row]
        for row in selected_block.get("table_rows") or []
    ]
    if not canonical_rows or not page_rows:
        raise ValueError("selected verified block does not contain table rows")
    patched_any = False
    for canonical_row in canonical_rows:
        for cell_index, cell in enumerate(list(canonical_row)):
            if not _SUSPICIOUS_MARKUP_RE.search(cell):
                continue
            candidates = [
                ( _row_overlap(canonical_row, page_row, skip=cell_index), page_row )
                for page_row in page_rows
                if len(page_row) > cell_index
            ]
            if not candidates:
                raise ValueError("verified table has no corresponding source row")
            candidates.sort(key=lambda item: item[0], reverse=True)
            score, matched_row = candidates[0]
            if score < 0.50:
                raise ValueError("verified table row does not match source context")
            replacement = _render_page_cell(matched_row[cell_index], selected_page).strip()
            suspicious_inner = re.sub(r"<[^>]+>", "", cell).strip()
            if (
                not replacement
                or _SUSPICIOUS_MARKUP_RE.search(replacement)
                or _normal(replacement) == _normal(suspicious_inner)
            ):
                raise ValueError("verified table cell does not resolve the converter anomaly")
            canonical_row[cell_index] = replacement
            patched_any = True
    if not patched_any:
        raise ValueError("canonical table no longer contains the reported anomaly")
    return "\n".join(" | ".join(row) for row in canonical_rows)


def _render_verified_page_block(
    page: dict[str, Any], block: dict[str, Any]
) -> str:
    kind = str(block.get("kind") or "")
    if kind == "table":
        return "\n".join(
            " | ".join(_render_page_cell(cell, page) for cell in row)
            for row in block.get("table_rows") or []
        )
    if kind == "math":
        latex = str(block.get("latex") or "").strip()
        return kr.katex(latex) if latex else ""
    if kind == "figure":
        return _render_page_visual_marker(
            page, int(block.get("reading_order") or 0)
        )
    return str(block.get("text") or "").strip()


def _resolve_verified_page_candidate(
    canonical_block: dict[str, Any],
    *,
    selected_page: dict[str, Any],
    selected_block: dict[str, Any],
) -> str:
    """Derive replacement text from verified page data without model prose."""

    canonical_text = str(
        canonical_block.get("display_text")
        or canonical_block.get("raw_text")
        or ""
    )
    if canonical_block.get("kind") == "table":
        if selected_block.get("kind") != "table":
            raise ValueError(
                "table anomaly must be resolved by a verified table block")
        resolved = _patch_suspicious_table(
            canonical_text,
            selected_block=selected_block,
            selected_page=selected_page,
        )
    else:
        resolved = _render_verified_page_block(
            selected_page, selected_block)
        source_tokens = _source_tokens(canonical_text)
        resolved_tokens = _source_tokens(resolved)
        if source_tokens and resolved_tokens:
            overlap = len(source_tokens & resolved_tokens) / max(
                1, min(len(source_tokens), len(resolved_tokens))
            )
            if overlap < 0.35:
                raise ValueError(
                    "verified replacement does not match the source block "
                    "context")
    cleaned = _clean_public_text(resolved)
    if (
        not cleaned
        or _SUSPICIOUS_MARKUP_RE.search(cleaned)
        or kr.rich_text_issues(cleaned)
    ):
        raise ValueError(
            "verified replacement violates semantic rich-text contract")
    return resolved


def _source_review_candidate_rows(
    canonical_block: dict[str, Any],
    *,
    page_bundle: dict[str, Any] | None,
    source_chars: int,
) -> list[dict[str, Any]]:
    """Return only candidate page blocks that can be applied deterministically."""

    if not isinstance(page_bundle, dict):
        return []
    packet = _candidate_anomaly_packet(
        canonical_block,
        page_bundle=page_bundle,
        source_chars=max(1, source_chars),
        limit=12,
    )
    candidates: list[dict[str, Any]] = []
    for row in packet.get("candidate_blocks") or []:
        if not isinstance(row, dict):
            continue
        target_id = str(row.get("block_key") or "")
        selected_page, selected_block = _page_and_block_by_key(
            page_bundle, target_id)
        if (
            not target_id
            or not isinstance(selected_page, dict)
            or not isinstance(selected_block, dict)
        ):
            continue
        try:
            resolved = _resolve_verified_page_candidate(
                canonical_block,
                selected_page=selected_page,
                selected_block=selected_block,
            )
        except (TypeError, ValueError):
            continue
        candidates.append({
            "target_id": target_id,
            "page_id": str(selected_page.get("page_id") or ""),
            "page_number": int(selected_page.get("page_number") or 0),
            "reading_order": int(selected_block.get("reading_order") or 0),
            "kind": str(selected_block.get("kind") or ""),
            "visible_text": _page_block_visible_text(selected_block)[:8_000],
            "resolved_text": resolved,
            "resolved_sha256": _sha256_text(resolved),
            "retrieval_score": float(row.get("retrieval_score") or 0.0),
        })
    return candidates[:24]


def _has_human_selected_source_overrides(graph: Any) -> bool:
    return bool(
        isinstance(graph, dict)
        and any(
            isinstance((row or {}).get("source_override"), dict)
            and (row["source_override"].get("mode")
                 == "human_selected_verified_pdf_block")
            for row in graph.get("blocks") or []
            if isinstance(row, dict)
        )
    )


def _current_human_source_overrides_valid(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
    page_bundle: dict[str, Any] | None,
) -> bool:
    """Re-derive every human override from the current verified PDF ACSD."""

    overrides = [
        row for row in graph.get("blocks") or []
        if isinstance(row, dict)
        and isinstance(row.get("source_override"), dict)
        and row["source_override"].get("mode")
        == "human_selected_verified_pdf_block"
    ]
    if not overrides:
        return True
    if not isinstance(page_bundle, dict):
        return False
    graph_pdf_sha256 = str(
        (graph.get("vision_evidence") or {}).get("pdf_sha256") or "")
    current_pdf_sha256 = str(page_bundle.get("pdf_sha256") or "")
    if not graph_pdf_sha256 or current_pdf_sha256 != graph_pdf_sha256:
        return False
    canonical_by_id = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or []
        if isinstance(row, dict)
    }
    source_chars = int(
        (canonical.get("document") or {}).get("source_chars") or 0)
    for graph_block in overrides:
        block_id = str(graph_block.get("block_id") or "")
        canonical_block = canonical_by_id.get(block_id)
        override = graph_block["source_override"]
        target_id = str(override.get("selected_block_key") or "")
        if not isinstance(canonical_block, dict) or not target_id:
            return False
        current_candidate = next(
            (
                row for row in _source_review_candidate_rows(
                    canonical_block,
                    page_bundle=page_bundle,
                    source_chars=source_chars,
                )
                if str(row.get("target_id") or "") == target_id
            ),
            None,
        )
        if not isinstance(current_candidate, dict):
            return False
        resolved = str(current_candidate.get("resolved_text") or "")
        if (
            not resolved
            or override.get("page_id") != current_candidate.get("page_id")
            or int(override.get("page_number") or 0)
            != int(current_candidate.get("page_number") or 0)
            or int(override.get("reading_order") or 0)
            != int(current_candidate.get("reading_order") or 0)
            or override.get("resolved_text") != resolved
            or override.get("resolved_sha256") != _sha256_text(resolved)
        ):
            return False
    return True


def _source_review_diagnostic_schema(
    target_ids: list[str],
) -> dict[str, Any]:
    return {
        "name": "aegis_phase3_source_review_diagnostic",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "diagnosis": {"type": "string"},
                "recommended_target_id": {
                    "type": "string",
                    "enum": ["NONE", *target_ids],
                },
                "candidate_reasons": {
                    "type": "array",
                    "maxItems": len(target_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "target_id": {
                                "type": "string",
                                "enum": target_ids or ["NONE"],
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["target_id", "reason"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "question",
                "diagnosis",
                "recommended_target_id",
                "candidate_reasons",
            ],
            "additionalProperties": False,
        },
    }


def _diagnose_source_review_via_openai(
    payload: dict[str, Any],
    *,
    source_path: Path | None,
) -> dict[str, Any]:
    """Make one bounded diagnosis; never repair or rewrite source text."""

    target_ids = [
        str(row.get("target_id") or "")
        for row in payload.get("candidates") or []
        if str(row.get("target_id") or "")
    ]
    system = (
        "You are the Aegis human-intervention diagnostician. Explain the exact "
        "source discrepancy in plain language and ask one concrete question "
        "that lets the user resolve it. You may recommend only one supplied "
        "verified page-block target_id, or NONE when evidence is insufficient. "
        "Do not transcribe, rewrite, repair, omit, merge, or invent source "
        "content. Preserve source identity, QIDs, Figures, task wording, source "
        "order, and the selected academic topic."
    )
    pages: list[phase22.EvidencePage] = []
    page_numbers = [
        int(row.get("page_number") or 0)
        for row in payload.get("candidates") or []
        if int(row.get("page_number") or 0) > 0
    ]
    if (
        source_path is not None
        and source_path.exists()
        and source_path.suffix.lower() == ".pdf"
        and page_numbers
    ):
        pages = _anomaly_evidence_pages(source_path, page_numbers)
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=pages,
        response_schema=_source_review_diagnostic_schema(target_ids),
        purpose="source_adjudication",
        max_tokens=4_000,
        single_attempt=True,
    )


def _custom_source_instruction_schema(
    target_ids: list[str],
) -> dict[str, Any]:
    return {
        "name": "aegis_phase3_custom_source_instruction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": [
                        "use_verified_page_block",
                        "needs_clarification",
                        "source_replacement_required",
                    ],
                },
                "selected_target_id": {
                    "type": "string",
                    "enum": ["NONE", *target_ids],
                },
                "question": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": [
                "decision", "selected_target_id", "question", "reason",
            ],
            "additionalProperties": False,
        },
    }


def _interpret_custom_source_instruction_via_openai(
    payload: dict[str, Any],
    *,
    instruction: str,
    source_path: Path | None,
) -> dict[str, Any]:
    """Interpret one human instruction once within supplied candidate IDs."""

    target_ids = [
        str(row.get("target_id") or "")
        for row in payload.get("candidates") or []
        if str(row.get("target_id") or "")
    ]
    system = (
        "Interpret the user's source-review instruction exactly once. Select "
        "only a supplied verified source-candidate target_id when the "
        "instruction unambiguously identifies it. A candidate may be a "
        "verified PDF block or a hash-sealed canonical-topic patch. Otherwise "
        "request clarification or say that the source must be replaced. Never "
        "invent replacement text, change raw source identity, or choose an ID "
        "that was not supplied."
    )
    pages: list[phase22.EvidencePage] = []
    page_numbers = [
        int(row.get("page_number") or 0)
        for row in payload.get("candidates") or []
        if int(row.get("page_number") or 0) > 0
    ]
    if (
        source_path is not None
        and source_path.exists()
        and source_path.suffix.lower() == ".pdf"
        and page_numbers
    ):
        pages = _anomaly_evidence_pages(source_path, page_numbers)
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(
            {
                "review": payload,
                "user_instruction": instruction,
            },
            ensure_ascii=False,
            indent=2,
        ),
        pages=pages,
        response_schema=_custom_source_instruction_schema(target_ids),
        purpose="source_adjudication",
        max_tokens=3_000,
        single_attempt=True,
    )


def reconcile_source_anomalies(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
    page_bundle: dict[str, Any] | None,
    source_path: Path | None = None,
    provider: AnomalyProvider | None = None,
    critic: AnomalyCritic | None = None,
    allow_automatic_reconciliation: bool = True,
) -> dict[str, Any]:
    """Repair converter-only semantic markup using verified PDF block IDs.

    The APIs select opaque evidence IDs only. Replacement text is derived
    deterministically from the already-verified page ACSD, so a model cannot
    improvise a formula, table cell, caption, or asset URL.
    """
    out = copy.deepcopy(graph)
    recomputed_codes = {
        "numbered_main_topic_coverage",
        "semantic_source_rich_text",
        "semantic_source_hash_mismatch",
        "unresolved_source_override_markup",
        "invalid_source_override_hash",
    }
    canonical_blocks = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or [] if isinstance(row, dict)
    }
    suspicious_ids = [
        str(block.get("block_id") or "")
        for block in out.get("blocks") or []
        if isinstance(block, dict)
        and _SUSPICIOUS_MARKUP_RE.search(
            str((canonical_blocks.get(str(block.get("block_id") or "")) or {}).get("raw_text") or "")
        )
    ]
    if not suspicious_ids:
        # Do not erase the only actionable validation issue before returning.
        # Re-render and derive status from the current graph so a stale
        # ``failed`` flag can never survive with an empty issue ledger.
        semantic_source = render_semantic_source(out, canonical)
        out["semantic_source_sha256"] = _sha256_text(semantic_source)
        retained = [
            issue for issue in out.get("issues") or []
            if issue.get("code") not in recomputed_codes
        ]
        errors = validate_graph(
            out,
            canonical=canonical,
            semantic_source=semantic_source,
        )
        out["issues"] = [*retained, *errors]
        out["status"] = (
            "failed"
            if errors
            else (
                "review_required"
                if any(
                    row.get("severity") == "error"
                    for row in retained
                    if isinstance(row, dict)
                )
                else "ready"
            )
        )
        return out
    if not isinstance(page_bundle, dict):
        return out
    if not allow_automatic_reconciliation:
        out["status"] = "review_required"
        out["issues"] = [
            issue for issue in out.get("issues") or []
            if (
                isinstance(issue, dict)
                and issue.get("code")
                != "converter_semantic_markup_requires_pdf_reconciliation"
            )
        ] + [{
            "code": "converter_semantic_markup_requires_pdf_reconciliation",
            "severity": "error",
            "block_ids": suspicious_ids,
            "message": (
                "Converter semantic markup requires one explicit human "
                "decision against verified original-PDF evidence."
            ),
        }]
        return out
    provider = provider or (
        (lambda packet: _select_source_anomaly_via_openai(
            packet, source_path=Path(source_path)
        ))
        if semantic_api_enabled() and source_path is not None else None
    )
    critic = critic or (
        (lambda payload: _critic_source_anomaly_via_openai(
            payload["packet"], payload["selection"], source_path=Path(source_path)
        ))
        if semantic_api_enabled() and source_path is not None else None
    )
    if provider is None or critic is None:
        return out
    out["issues"] = [
        issue for issue in out.get("issues") or []
        if issue.get("code") not in recomputed_codes
    ]
    repairs: list[dict[str, Any]] = []
    unresolved: list[str] = []
    graph_block_by_id = {
        str(row.get("block_id") or ""): row
        for row in out.get("blocks") or [] if isinstance(row, dict)
    }
    source_chars = int((canonical.get("document") or {}).get("source_chars") or 0)
    for block_id in suspicious_ids:
        canonical_block = canonical_blocks.get(block_id)
        graph_block = graph_block_by_id.get(block_id)
        if not isinstance(canonical_block, dict) or not isinstance(graph_block, dict):
            unresolved.append(block_id)
            continue
        packet = _candidate_anomaly_packet(
            canonical_block,
            page_bundle=page_bundle,
            source_chars=max(1, source_chars),
        )
        keys = {
            str(row.get("block_key") or "")
            for row in packet.get("candidate_blocks") or []
        }
        if not keys:
            unresolved.append(block_id)
            continue
        selection = provider(copy.deepcopy(packet))
        selected_key = str((selection or {}).get("selected_block_key") or "")
        if (
            not isinstance(selection, dict)
            or selection.get("decision") != "use_verified_page_block"
            or selected_key not in keys
            or not confidence_policy.accepts(
                selection.get("confidence"),
                confidence_policy.ConfidenceGate.SOURCE_CRITICAL,
            )
        ):
            unresolved.append(block_id)
            continue
        verification = critic({
            "packet": copy.deepcopy(packet),
            "selection": copy.deepcopy(selection),
        })
        if (
            not isinstance(verification, dict)
            or verification.get("verdict") != "verified"
            or str(verification.get("selected_block_key") or "") != selected_key
            or not confidence_policy.accepts(
                verification.get("confidence"),
                confidence_policy.ConfidenceGate.SOURCE_CRITICAL,
            )
            or bool(verification.get("issues"))
        ):
            unresolved.append(block_id)
            continue
        selected_page, selected_block = _page_and_block_by_key(
            page_bundle, selected_key
        )
        if not isinstance(selected_page, dict) or not isinstance(selected_block, dict):
            unresolved.append(block_id)
            continue
        try:
            canonical_text = str(
                canonical_block.get("display_text")
                or canonical_block.get("raw_text")
                or ""
            )
            resolved = _resolve_verified_page_candidate(
                canonical_block,
                selected_page=selected_page,
                selected_block=selected_block,
            )
        except (TypeError, ValueError):
            unresolved.append(block_id)
            continue
        graph_block["source_override"] = {
            "mode": "verified_pdf_block_patch",
            "canonical_block_raw_sha256": str(
                canonical_block.get("raw_sha256") or _sha256_text(canonical_text)
            ),
            "page_id": str(selected_page.get("page_id") or ""),
            "page_number": int(selected_page.get("page_number") or 0),
            "reading_order": int(selected_block.get("reading_order") or 0),
            "selected_block_key": selected_key,
            "resolved_text": resolved,
            "resolved_sha256": _sha256_text(resolved),
            "selection_confidence": float(selection.get("confidence") or 0.0),
            "verification_confidence": float(verification.get("confidence") or 0.0),
        }
        repairs.append({
            "block_id": block_id,
            "selected_block_key": selected_key,
            "page_number": int(selected_page.get("page_number") or 0),
        })
    out["source_fusion_repairs"] = repairs
    if unresolved:
        out["status"] = "review_required"
        out["issues"] = [
            issue for issue in out.get("issues") or []
            if issue.get("code") != "converter_semantic_markup_requires_pdf_reconciliation"
        ] + [{
            "code": "converter_semantic_markup_requires_pdf_reconciliation",
            "severity": "error",
            "block_ids": unresolved,
            "message": (
                "Original-PDF source fusion could not safely resolve every "
                "converter semantic-markup anomaly."
            ),
        }]
    else:
        out["status"] = "ready"
        out["issues"] = [
            issue for issue in out.get("issues") or []
            if issue.get("code") != "converter_semantic_markup_requires_pdf_reconciliation"
        ]
        out["issues"].append({
            "code": "converter_semantic_markup_repaired_from_verified_pdf",
            "severity": "warning",
            "block_ids": suspicious_ids,
            "message": (
                "Converter-only semantic markup was replaced using independently "
                "verified original-PDF page blocks and deterministic rendering."
            ),
        })
    semantic_source = render_semantic_source(out, canonical)
    out["semantic_source_sha256"] = _sha256_text(semantic_source)
    errors = validate_graph(out, canonical=canonical, semantic_source=semantic_source)
    if errors:
        out["status"] = "failed"
        out.setdefault("issues", []).extend(errors)
    return out


def _source_review_block_ids(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
) -> list[str]:
    """Return unresolved source-block IDs in canonical order."""

    graph_blocks = {
        str(row.get("block_id") or ""): row
        for row in graph.get("blocks") or []
        if isinstance(row, dict)
    }

    def unresolved_override(block_id: str) -> bool:
        override = (graph_blocks.get(block_id) or {}).get("source_override")
        resolved = (
            str(override.get("resolved_text") or "")
            if isinstance(override, dict)
            else ""
        )
        return bool(
            not resolved
            or _SUSPICIOUS_MARKUP_RE.search(resolved)
            or kr.rich_text_issues(_clean_public_text(resolved))
        )

    requested: set[str] = set()
    for issue in graph.get("issues") or []:
        if not isinstance(issue, dict) or issue.get("severity") != "error":
            continue
        requested.update(
            str(value)
            for value in issue.get("block_ids") or []
            if str(value) and unresolved_override(str(value))
        )
    for block in canonical.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_id = str(block.get("block_id") or "")
        if (
            _SUSPICIOUS_MARKUP_RE.search(str(block.get("raw_text") or ""))
            and unresolved_override(block_id)
        ):
            requested.add(block_id)
    canonical_order = [
        str(row.get("block_id") or "")
        for row in sorted(
            [
                row for row in canonical.get("blocks") or []
                if isinstance(row, dict)
            ],
            key=lambda row: int(row.get("order") or 0),
        )
        if str(row.get("block_id") or "")
    ]
    return [block_id for block_id in canonical_order if block_id in requested]


def _rich_text_issue_block_ids(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
) -> list[str]:
    """Localize semantic rich-text failures to immutable ACSD block IDs."""

    graph_blocks = {
        str(row.get("block_id") or ""): row
        for row in graph.get("blocks") or []
        if isinstance(row, dict)
    }
    affected: list[str] = []
    for block in sorted(
        [
            row for row in canonical.get("blocks") or []
            if isinstance(row, dict)
        ],
        key=lambda row: int(row.get("order") or 0),
    ):
        if str(block.get("kind") or "") in {"layout", "heading"}:
            continue
        block_id = str(block.get("block_id") or "")
        text = _graph_block_text(graph_blocks.get(block_id), block)
        cleaned = _clean_public_text(text)
        if kr.rich_text_issues(cleaned):
            affected.append(block_id)
    return affected


def _topic_heading_preview(graph: dict[str, Any]) -> str:
    """Render a compact, reviewable view of the working MMD topic spine."""

    lines: list[str] = []
    for topic in sorted(
        [row for row in graph.get("topics") or [] if isinstance(row, dict)],
        key=lambda row: (
            int(row.get("source_start") or 0),
            int(row.get("order") or 0),
        ),
    ):
        number = str(topic.get("structural_number") or "").strip()
        title = str(topic.get("title") or "").strip()
        if title:
            lines.append(f"## {number + ' ' if number else ''}{title}")
    return "\n".join(lines)[:16_000]


def _graph_source_identity_inventory(graph: dict[str, Any]) -> dict[str, Any]:
    """Return identities a derived-source topology patch may never change."""

    return {
        "source_contract_hash": str(graph.get("source_contract_hash") or ""),
        "semantic_context_hash": str(
            graph.get("semantic_context_hash") or ""
        ),
        "source_sha256": str(graph.get("source_sha256") or ""),
        "blocks": [
            {
                key: copy.deepcopy(row.get(key))
                for key in (
                    "block_id", "order", "kind", "source_start", "source_end",
                    "raw_sha256", "figure_id", "image_ids", "math_ids",
                    "task_ids",
                )
            }
            for row in graph.get("blocks") or []
            if isinstance(row, dict)
        ],
        "tasks": [
            {
                key: copy.deepcopy(row.get(key))
                for key in (
                    "task_id", "qid", "order", "section_id", "source_start",
                    "source_end", "source_label", "source_kind",
                    "identity_key", "display_prompt", "figure_ids",
                    "image_urls", "requires_visual", "chapter_wide",
                )
            }
            for row in graph.get("tasks") or []
            if isinstance(row, dict)
        ],
        "figures": copy.deepcopy(graph.get("figures") or []),
        "images": copy.deepcopy(graph.get("images") or []),
        "math": copy.deepcopy(graph.get("math") or []),
    }


def _numbered_topic_patch_preview(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
    page_bundle: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Rebuild only topic ownership from sealed canonical heading evidence.

    Existing API/critic classifications are replayed as opaque section roles;
    no provider call runs. The rebuild must preserve every source-owned block,
    task, QID, Figure, image, math span and raw-source hash, then pass the full
    graph validator before it can become a selectable patch.
    """

    mismatches = _numbered_main_topic_mismatches(
        graph, canonical=canonical)
    if not mismatches:
        return None
    current_errors = validate_graph(
        graph,
        canonical=canonical,
        semantic_source=render_semantic_source(graph, canonical),
        allow_unresolved_source_anomalies=True,
    )
    error_codes = {
        str(row.get("code") or "")
        for row in current_errors
        if isinstance(row, dict)
    }
    if error_codes != {"numbered_main_topic_coverage"}:
        return None

    existing_sections = {
        str(row.get("section_id") or ""): copy.deepcopy(row)
        for row in graph.get("sections") or []
        if isinstance(row, dict) and str(row.get("section_id") or "")
    }

    def preserved_hierarchy(payload: dict[str, Any]) -> dict[str, Any]:
        rows = [
            row for row in payload.get("sections") or []
            if isinstance(row, dict) and str(row.get("section_id") or "")
        ]
        allowed_ids = {str(row.get("section_id") or "") for row in rows}
        preserved: list[dict[str, Any]] = []
        for row in rows:
            section_id = str(row.get("section_id") or "")
            prior = existing_sections.get(section_id, {})
            parent = str(prior.get("parent_section_id") or "")
            if parent not in allowed_ids:
                parent = ""
            preserved.append({
                "section_id": section_id,
                "role": str(
                    prior.get("role") or row.get("baseline_role") or "other"
                ),
                "parent_section_id": parent,
                "confidence": float(prior.get("confidence") or 1.0),
                "evidence": list(
                    prior.get("evidence") or ["preserved verified hierarchy"]
                )[:4],
            })
        return {"sections": preserved}

    source_length = max(
        int((canonical.get("document") or {}).get("source_chars") or 0),
        max(
            [
                int(row.get("source_end") or 0)
                for row in canonical.get("blocks") or []
                if isinstance(row, dict)
            ]
            or [0]
        ),
        1,
    )
    rebuilt, _report = compile_semantic_graph(
        canonical,
        source_text=" " * source_length,
        metadata=copy.deepcopy(graph.get("metadata") or {}),
        page_bundle=page_bundle,
        hierarchy_provider=preserved_hierarchy,
        critic_provider=None,
    )
    rebuilt["classification_mode"] = str(
        graph.get("classification_mode") or rebuilt.get("classification_mode")
    )
    if (
        _sha256_json(_graph_source_identity_inventory(rebuilt))
        != _sha256_json(_graph_source_identity_inventory(graph))
    ):
        raise ValueError(
            "canonical topic patch changed a source-owned identity inventory"
        )

    prior_blocks = {
        str(row.get("block_id") or ""): row
        for row in graph.get("blocks") or []
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
    for row in rebuilt.get("blocks") or []:
        prior = prior_blocks.get(str(row.get("block_id") or ""), {})
        if isinstance(prior.get("source_override"), dict):
            row["source_override"] = copy.deepcopy(prior["source_override"])
    for key in (
        "source_fusion_repairs",
        "source_review_resolution",
        _MACHINE_METADATA_MIGRATION_KEY,
    ):
        if key in graph:
            rebuilt[key] = copy.deepcopy(graph[key])

    before_source = render_semantic_source(graph, canonical)
    after_source = render_semantic_source(rebuilt, canonical)
    rebuilt["semantic_source_sha256"] = _sha256_text(after_source)
    errors = validate_graph(
        rebuilt,
        canonical=canonical,
        semantic_source=after_source,
    )
    if errors:
        raise ValueError(
            "canonical topic patch did not pass full semantic graph validation: "
            + "; ".join(str(row.get("code") or "") for row in errors)
        )
    retained_warnings = [
        copy.deepcopy(row)
        for row in graph.get("issues") or []
        if isinstance(row, dict) and row.get("severity") != "error"
    ]
    operations = [
        (
            f"Restore numbered main topic {row.get('number')} "
            f"{row.get('expected_title')} at canonical section "
            f"{row.get('resolved_section_id')}"
        ).strip()
        for row in mismatches
    ]
    patch_material = {
        "version": _NUMBERED_TOPIC_PATCH_VERSION,
        "kind": "canonical_topic_binding",
        "target": "working_derived_source",
        "raw_source_mutated": False,
        "source_contract_hash": str(graph.get("source_contract_hash") or ""),
        "semantic_context_hash": str(
            graph.get("semantic_context_hash") or ""
        ),
        "before_sha256": _sha256_text(before_source),
        "after_sha256": _sha256_text(after_source),
        "operations": operations,
    }
    patch_hash = _sha256_json(patch_material)
    target_id = f"canonical-topic-patch-{patch_hash[:24]}"
    source_patch = {
        **patch_material,
        "verified": True,
        "patch_hash": patch_hash,
        "target_id": target_id,
        "before": _topic_heading_preview(graph),
        "after": _topic_heading_preview(rebuilt),
    }
    rebuilt["issues"] = [
        *retained_warnings,
        {
            "severity": "warning",
            "code": "numbered_main_topic_coverage_repaired",
            "message": (
                "The working semantic source topic spine was rebound from "
                "sealed canonical heading evidence; the raw MMD was unchanged."
            ),
            "operations": operations,
        },
    ]
    rebuilt["status"] = "ready"
    rebuilt["numbered_topic_patch_resolution"] = {
        **copy.deepcopy(source_patch),
        "before": "",
        "after": "",
    }
    rebuilt[_DOWNSTREAM_INVALIDATION_KEY] = True
    return rebuilt, source_patch


def _human_reviewable_source_graph(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
    page_bundle: dict[str, Any] | None = None,
) -> bool:
    """Only sealed source candidates may enter the decision workflow."""

    allowed = {
        "converter_semantic_markup_requires_pdf_reconciliation",
        "semantic_source_rich_text",
    }
    errors = [
        row for row in graph.get("issues") or []
        if isinstance(row, dict) and row.get("severity") == "error"
    ]
    if not errors:
        return False
    error_codes = {str(row.get("code") or "") for row in errors}
    if error_codes == {"numbered_main_topic_coverage"}:
        try:
            return _numbered_topic_patch_preview(
                graph,
                canonical=canonical,
                page_bundle=page_bundle,
            ) is not None
        except (TypeError, ValueError):
            return False
    if any(code not in allowed for code in error_codes):
        return False
    localized = set(_source_review_block_ids(graph, canonical=canonical))
    return bool(localized) and all(
        bool({
            str(value)
            for value in row.get("block_ids") or []
            if str(value)
        } & localized)
        for row in errors
    )


def _source_review_item_context(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
    block_id: str,
) -> dict[str, Any]:
    canonical_block = next(
        (
            row for row in canonical.get("blocks") or []
            if isinstance(row, dict)
            and str(row.get("block_id") or "") == block_id
        ),
        {},
    )
    graph_block = next(
        (
            row for row in graph.get("blocks") or []
            if isinstance(row, dict)
            and str(row.get("block_id") or "") == block_id
        ),
        {},
    )
    topic_id = str(graph_block.get("topic_id") or "")
    topic = next(
        (
            row for row in graph.get("topics") or []
            if isinstance(row, dict)
            and str(row.get("topic_id") or "") == topic_id
        ),
        {},
    )
    task_ids = {
        str(value)
        for value in canonical_block.get("task_ids") or []
        if str(value)
    }
    tasks = [
        row for row in graph.get("tasks") or []
        if isinstance(row, dict)
        and (
            str(row.get("task_id") or "") in task_ids
            or (
                int(canonical_block.get("source_start") or 0)
                <= int(row.get("source_start") or 0)
                < int(canonical_block.get("source_end") or 0)
            )
        )
    ]
    return {
        "block_id": block_id,
        "kind": str(canonical_block.get("kind") or ""),
        "raw_text": str(
            canonical_block.get("display_text")
            or canonical_block.get("raw_text")
            or ""
        )[:10_000],
        "raw_sha256": str(
            canonical_block.get("raw_sha256")
            or _sha256_text(canonical_block.get("raw_text") or "")
        ),
        "topic_id": topic_id,
        "topic": str(topic.get("title") or ""),
        "qids": [
            str(row.get("qid") or "")
            for row in tasks[:100]
            if str(row.get("qid") or "")
        ],
        "questions": [
            str(row.get("display_prompt") or "")
            for row in tasks[:100]
            if str(row.get("display_prompt") or "")
        ],
    }


def _source_review_context_hash(
    graph: dict[str, Any],
    *,
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
    pdf_sha256: str,
    revision: dict[str, Any] | None = None,
) -> str:
    return _sha256_json({
        "version": _SOURCE_REVIEW_VERSION,
        "source_contract_hash": str(graph.get("source_contract_hash") or ""),
        "semantic_context_hash": str(
            graph.get("semantic_context_hash") or ""),
        "pdf_sha256": pdf_sha256,
        "item": {
            "block_id": item.get("block_id"),
            "raw_sha256": item.get("raw_sha256"),
            "topic_id": item.get("topic_id"),
            "qids": item.get("qids") or [],
        },
        "candidates": [
            {
                "target_id": row.get("target_id"),
                "resolved_sha256": row.get("resolved_sha256"),
            }
            for row in candidates
        ],
        "revision": copy.deepcopy(revision or {}),
    })


def _build_source_review_state(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
    page_bundle: dict[str, Any] | None,
    source_path: Path | None,
    block_id: str,
    revision: dict[str, Any] | None = None,
    allow_diagnostic_call: bool = True,
) -> dict[str, Any]:
    canonical_block = next(
        (
            row for row in canonical.get("blocks") or []
            if isinstance(row, dict)
            and str(row.get("block_id") or "") == block_id
        ),
        {},
    )
    item = _source_review_item_context(
        graph, canonical=canonical, block_id=block_id)
    graph_errors = [{
        "code": str(row.get("code") or "source_review_required"),
        "message": str(row.get("message") or "")[:8_000],
        "block_ids": [
            str(value) for value in row.get("block_ids") or []
            if str(value)
        ][:100],
        "page": str(
            row.get("page_number") or row.get("page") or ""),
    } for row in graph.get("issues") or []
        if isinstance(row, dict)
        and row.get("severity") == "error"
        and (
            not block_id
            or not row.get("block_ids")
            or block_id in {
                str(value) for value in row.get("block_ids") or []
            }
        )
    ][:20]
    candidates = _source_review_candidate_rows(
        canonical_block,
        page_bundle=page_bundle,
        source_chars=int(
            (canonical.get("document") or {}).get("source_chars") or 0),
    )
    pdf_sha256 = str((page_bundle or {}).get("pdf_sha256") or "")
    context_hash = _source_review_context_hash(
        graph,
        item=item,
        candidates=candidates,
        pdf_sha256=pdf_sha256,
        revision=revision,
    )
    payload = {
        "source_contract_hash": str(graph.get("source_contract_hash") or ""),
        "semantic_context_hash": str(
            graph.get("semantic_context_hash") or ""),
        "issue": {
            "code": str(
                (graph_errors[0] if graph_errors else {}).get("code")
                or "semantic_source_graph_requires_review"
            ),
            "block_id": block_id,
            "topic": item.get("topic") or "",
            "qids": item.get("qids") or [],
            "questions": item.get("questions") or [],
            "source_text": item.get("raw_text") or "",
            "errors": graph_errors,
        },
        "candidates": [
            {
                "target_id": row["target_id"],
                "page_number": row["page_number"],
                "kind": row["kind"],
                "visible_text": row["visible_text"],
            }
            for row in candidates
        ],
    }
    diagnostic: dict[str, Any] = {}
    if allow_diagnostic_call and semantic_api_enabled():
        try:
            diagnostic = _diagnose_source_review_via_openai(
                copy.deepcopy(payload),
                source_path=source_path,
            )
        except Exception as exc:
            progress.log(
                "The one bounded source-review diagnosis could not complete; "
                "manual verified-evidence choices remain available and no "
                "automatic retry will run. "
                f"Reason: {str(exc)[:500]}",
                level="warning",
            )
    target_ids = {
        str(row.get("target_id") or "") for row in candidates
        if str(row.get("target_id") or "")
    }
    recommended = str(
        diagnostic.get("recommended_target_id") or "")
    if recommended == "NONE" or recommended not in target_ids:
        recommended = ""
    reason_by_target = {
        str(row.get("target_id") or ""): str(row.get("reason") or "")
        for row in diagnostic.get("candidate_reasons") or []
        if isinstance(row, dict)
        and str(row.get("target_id") or "") in target_ids
    }
    for candidate in candidates:
        candidate["reason"] = reason_by_target.get(
            str(candidate.get("target_id") or ""),
            (
                "This independently extracted PDF block is a bounded "
                "source-evidence candidate."
            ),
        )
    diagnosis = str(diagnostic.get("diagnosis") or "").strip()
    if not diagnosis:
        exact = "; ".join(
            f"{row['code']}: {row['message']}"
            for row in graph_errors
            if row.get("message")
        )
        diagnosis = exact or (
            "The converted source contains content that cannot be certified "
            "against the verified original-PDF evidence automatically."
        )
    question = str(diagnostic.get("question") or "").strip()
    if not question:
        question = (
            "Which verified original-PDF block should replace this disputed "
            "converter block?"
            if candidates
            else (
                "No safe verified page-block candidate is available. Please "
                "give a source-specific instruction or replace the source file."
            )
        )
    return {
        "version": _SOURCE_REVIEW_VERSION,
        "decision_id": f"phase3-source-{context_hash[:24]}",
        "context_hash": context_hash,
        "pdf_sha256": pdf_sha256,
        "item": item,
        "graph_errors": graph_errors,
        "diagnosis": diagnosis[:8_000],
        "decision_question": question[:8_000],
        "recommended_target_id": recommended,
        "candidates": candidates,
        "custom_interpretation_used": bool(
            (revision or {}).get("custom_interpretation_used")),
        "revision": copy.deepcopy(revision or {}),
    }


def _build_numbered_topic_patch_review_state(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
    page_bundle: dict[str, Any] | None,
    revision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one hash-sealed working-source patch decision."""

    preview = _numbered_topic_patch_preview(
        graph,
        canonical=canonical,
        page_bundle=page_bundle,
    )
    if preview is None:
        raise ValueError(
            "numbered main-topic coverage has no safe canonical patch"
        )
    _repaired, source_patch = preview
    mismatches = _numbered_main_topic_mismatches(
        graph, canonical=canonical)
    missing_names = [
        f"{row.get('number')} {row.get('expected_title')}".strip()
        for row in mismatches
    ]
    first = mismatches[0] if mismatches else {}
    item = {
        "block_id": str(first.get("block_id") or "CANONICAL-TOPIC-SPINE"),
        "kind": "canonical_topic_binding",
        "raw_text": str(source_patch.get("before") or ""),
        "raw_sha256": _sha256_text(source_patch.get("before") or ""),
        "topic_id": "",
        "topic": "Canonical chapter topic spine",
        "qids": [],
        "questions": missing_names,
    }
    target_id = str(source_patch.get("target_id") or "")
    after_preview = str(source_patch.get("after") or "")
    candidates = [{
        "target_id": target_id,
        "page_id": "",
        "page_number": 0,
        "reading_order": 0,
        "kind": "canonical_topic_patch",
        "visible_text": after_preview,
        "resolved_text": after_preview,
        "resolved_sha256": _sha256_text(after_preview),
        "reason": (
            "This patch is derived from the existing numbered canonical "
            "headings and preserves every raw-source identity inventory."
        ),
    }]
    pdf_sha256 = str(
        (page_bundle or {}).get("pdf_sha256")
        or (graph.get("vision_evidence") or {}).get("pdf_sha256")
        or ""
    )
    context_hash = _source_review_context_hash(
        graph,
        item=item,
        candidates=candidates,
        pdf_sha256=pdf_sha256,
        revision=revision,
    )
    return {
        "version": _SOURCE_REVIEW_VERSION,
        "review_kind": "canonical_topic_patch",
        "decision_id": f"phase3-source-{context_hash[:24]}",
        "context_hash": context_hash,
        "pdf_sha256": pdf_sha256,
        "item": item,
        "graph_errors": [
            copy.deepcopy(row)
            for row in graph.get("issues") or []
            if isinstance(row, dict)
            and row.get("severity") == "error"
            and row.get("code") == "numbered_main_topic_coverage"
        ][:20],
        "diagnosis": (
            "The working Phase 3 graph lost or misbound a numbered main "
            "topic even though the sealed canonical source still identifies "
            "it. The proposed patch restores only the derived topic spine; "
            "the uploaded raw MMD remains byte-for-byte unchanged."
        ),
        "decision_question": (
            "Apply the verified canonical-source patch to the working MMD and "
            "resume, give different repair guidance, or replace the source?"
        ),
        "recommended_target_id": target_id,
        "candidates": candidates,
        "custom_interpretation_used": bool(
            (revision or {}).get("custom_interpretation_used")
        ),
        "revision": copy.deepcopy(revision or {}),
        "source_patch": copy.deepcopy(source_patch),
    }


def _source_review_pending(
    graph: dict[str, Any],
    state: dict[str, Any],
    *,
    canonical: dict[str, Any],
) -> dict[str, Any]:
    item = dict(state.get("item") or {})
    candidates = [
        row for row in state.get("candidates") or []
        if isinstance(row, dict)
    ]
    recommended = str(state.get("recommended_target_id") or "")
    if state.get("review_kind") == "canonical_topic_patch":
        source_patch = copy.deepcopy(state.get("source_patch") or {})
        public_candidates = [{
            "target_id": str(row.get("target_id") or ""),
            "concept_id": str(row.get("target_id") or "")[:256],
            "title": "Repair the working MMD topic spine",
            "topic": "Canonical chapter topic spine",
            "coverage": str(source_patch.get("after") or "")[:8_000],
            "gap": (
                "The current working MMD topic spine is missing or misbinding "
                "a numbered main topic."
            ),
        } for row in candidates[:1]]
        options = [{
            "choice": "accept_recommended",
            "label": "Apply the verified working-source patch",
            "recommended": True,
            "target_id": recommended,
            "target_concept_id": recommended[:256],
        }]
        if not state.get("custom_interpretation_used"):
            options.append({
                "choice": "custom_instruction",
                "label": "Tell Aegis how to correct the working source",
                "recommended": False,
            })
        options.append({
            "choice": "replace_source",
            "label": "Upload a different source instead",
            "recommended": False,
        })
        evidence_id = (
            "CANONICAL-PATCH-"
            + str(source_patch.get("patch_hash") or "")[:24].upper()
        )
        return {
            "decision_id": str(state.get("decision_id") or ""),
            "context_hash": str(state.get("context_hash") or ""),
            "kind": "phase3_source_graph_review",
            "phase": "3",
            "conflict": str(state.get("decision_question") or ""),
            "diagnosis": str(state.get("diagnosis") or ""),
            "decision_question": str(
                state.get("decision_question") or ""
            ),
            "item": {
                "unit_id": str(item.get("block_id") or ""),
                "type_id": "numbered_main_topic_coverage",
                "type_title": "Working-source topic patch",
                "qids": [],
                "questions": list(item.get("questions") or [])[:100],
                "topic": "Canonical chapter topic spine",
            },
            "candidates": public_candidates,
            "evidence": [
                {
                    "evidence_id": evidence_id,
                    "page": "",
                    "label": "Current working MMD topic spine",
                    "text": str(source_patch.get("before") or "")[:8_000],
                },
                {
                    "evidence_id": evidence_id + "-AFTER",
                    "page": "",
                    "label": "Verified patched topic spine",
                    "text": str(source_patch.get("after") or "")[:8_000],
                },
            ],
            "deferred_assignment_unit_ids": [],
            "options": options,
            "source_patch": source_patch,
        }
    options: list[dict[str, Any]] = []
    if recommended and any(
        str(row.get("target_id") or "") == recommended
        for row in candidates
    ):
        options.append({
            "choice": "accept_recommended",
            "label": "Use the recommended verified PDF evidence",
            "recommended": True,
            "target_id": recommended,
            # Older clients display this field for candidate-backed choices.
            "target_concept_id": recommended[:256],
        })
    if candidates:
        options.append({
            "choice": "select_candidate",
            "label": "Choose another verified PDF evidence block",
            "recommended": False,
        })
    if not state.get("custom_interpretation_used"):
        options.append({
            "choice": "custom_instruction",
            "label": "Tell Aegis what to do",
            "recommended": False,
        })
    options.append({
        "choice": "replace_source",
        "label": "Replace or correct the source file",
        "recommended": not candidates,
    })
    remaining = _source_review_block_ids(graph, canonical=canonical)
    public_candidates = [{
        "target_id": str(row.get("target_id") or ""),
        "concept_id": str(row.get("target_id") or "")[:256],
        "title": (
            f"PDF page {int(row.get('page_number') or 0)} · "
            f"{str(row.get('kind') or 'source block')}"
        ),
        "topic": str(item.get("topic") or ""),
        "coverage": str(row.get("visible_text") or "")[:8_000],
        "gap": str(row.get("reason") or "")[:8_000],
    } for row in candidates[:100]]
    evidence = [{
        "page": "",
        "label": str(item.get("block_id") or "source graph"),
        "text": str(item.get("raw_text") or "")[:8_000],
    }]
    evidence.extend({
        "page": str(row.get("page") or ""),
        "label": str(row.get("code") or "source graph error"),
        "text": str(row.get("message") or "")[:8_000],
    } for row in state.get("graph_errors") or []
        if isinstance(row, dict)
    )
    evidence.extend({
        "page": str(int(row.get("page_number") or 0) or ""),
        "label": str(row.get("target_id") or ""),
        "text": str(row.get("visible_text") or "")[:8_000],
    } for row in candidates[:23])
    issue_code = str(
        next(
            (
                row.get("code")
                for row in state.get("graph_errors") or []
                if isinstance(row, dict) and row.get("code")
            ),
            "",
        )
        or "semantic_source_graph_requires_review"
    )
    return {
        "decision_id": str(state.get("decision_id") or ""),
        "context_hash": str(state.get("context_hash") or ""),
        "kind": "phase3_source_graph_review",
        "phase": "3",
        "conflict": str(
            state.get("decision_question")
            or state.get("diagnosis")
            or "Source review is required."
        ),
        "diagnosis": str(state.get("diagnosis") or ""),
        "decision_question": str(state.get("decision_question") or ""),
        "item": {
            "unit_id": str(item.get("block_id") or ""),
            "type_id": issue_code,
            "type_title": "Source graph discrepancy",
            "qids": list(item.get("qids") or [])[:100],
            "questions": list(item.get("questions") or [])[:100],
            "topic": str(item.get("topic") or ""),
        },
        "candidates": public_candidates,
        "evidence": evidence[:100],
        "deferred_assignment_unit_ids": [
            value for value in remaining
            if value != str(item.get("block_id") or "")
        ][:5_000],
        "options": options,
    }


def _apply_human_source_candidate(
    graph: dict[str, Any],
    state: dict[str, Any],
    *,
    canonical: dict[str, Any],
    page_bundle: dict[str, Any] | None,
    target_id: str,
) -> dict[str, Any]:
    """Re-derive and apply one user-selected current-PDF block API-free."""

    candidate = next(
        (
            row for row in state.get("candidates") or []
            if isinstance(row, dict)
            and str(row.get("target_id") or "") == target_id
        ),
        None,
    )
    if not isinstance(candidate, dict):
        raise ValueError(
            "the selected source-review target is not a supplied candidate")
    expected_pdf_sha256 = str(state.get("pdf_sha256") or "")
    graph_pdf_sha256 = str(
        (graph.get("vision_evidence") or {}).get("pdf_sha256") or "")
    current_pdf_sha256 = str(
        (page_bundle or {}).get("pdf_sha256") or "")
    if (
        not isinstance(page_bundle, dict)
        or not expected_pdf_sha256
        or current_pdf_sha256 != expected_pdf_sha256
        or graph_pdf_sha256 != expected_pdf_sha256
    ):
        raise ValueError(
            "the verified original-PDF evidence changed after the source "
            "decision was shown")
    block_id = str((state.get("item") or {}).get("block_id") or "")
    canonical_block = next(
        (
            row for row in canonical.get("blocks") or []
            if isinstance(row, dict)
            and str(row.get("block_id") or "") == block_id
        ),
        None,
    )
    graph_block = next(
        (
            row for row in graph.get("blocks") or []
            if isinstance(row, dict)
            and str(row.get("block_id") or "") == block_id
        ),
        None,
    )
    if not isinstance(canonical_block, dict) or not isinstance(graph_block, dict):
        raise ValueError(
            "the disputed source block no longer exists in this source graph")
    current_candidate = next(
        (
            row for row in _source_review_candidate_rows(
                canonical_block,
                page_bundle=page_bundle,
                source_chars=int(
                    (canonical.get("document") or {}).get(
                        "source_chars") or 0),
            )
            if str(row.get("target_id") or "") == target_id
        ),
        None,
    )
    if (
        not isinstance(current_candidate, dict)
        or current_candidate.get("resolved_sha256")
        != candidate.get("resolved_sha256")
    ):
        raise ValueError(
            "the selected source-review target is no longer a current "
            "deterministic evidence candidate")
    selected_page, selected_block = _page_and_block_by_key(
        page_bundle, target_id)
    if (
        not isinstance(selected_page, dict)
        or not isinstance(selected_block, dict)
        or str(selected_page.get("page_id") or "")
        != str(current_candidate.get("page_id") or "")
        or int(selected_page.get("page_number") or 0)
        != int(current_candidate.get("page_number") or 0)
        or int(selected_block.get("reading_order") or 0)
        != int(current_candidate.get("reading_order") or 0)
    ):
        raise ValueError(
            "the selected verified PDF block changed after the decision was "
            "shown")
    resolved = _resolve_verified_page_candidate(
        canonical_block,
        selected_page=selected_page,
        selected_block=selected_block,
    )
    if (
        not resolved
        or candidate.get("resolved_sha256") != _sha256_text(resolved)
        or _SUSPICIOUS_MARKUP_RE.search(resolved)
        or kr.rich_text_issues(_clean_public_text(resolved))
    ):
        raise ValueError(
            "the selected source-review candidate no longer satisfies the "
            "verified rich-text contract")
    expected_raw_hash = str(
        (state.get("item") or {}).get("raw_sha256") or "")
    actual_raw_hash = str(
        canonical_block.get("raw_sha256")
        or _sha256_text(canonical_block.get("raw_text") or "")
    )
    if not expected_raw_hash or expected_raw_hash != actual_raw_hash:
        raise ValueError(
            "the disputed source block changed after the decision was shown")
    graph_block["source_override"] = {
        "mode": "human_selected_verified_pdf_block",
        "canonical_block_raw_sha256": actual_raw_hash,
        "page_id": str(current_candidate.get("page_id") or ""),
        "page_number": int(current_candidate.get("page_number") or 0),
        "reading_order": int(current_candidate.get("reading_order") or 0),
        "selected_block_key": target_id,
        "resolved_text": resolved,
        "resolved_sha256": _sha256_text(resolved),
        "human_decision_id": str(state.get("decision_id") or ""),
    }
    repairs = [
        copy.deepcopy(row)
        for row in graph.get("source_fusion_repairs") or []
        if isinstance(row, dict)
        and str(row.get("block_id") or "") != block_id
    ]
    repairs.append({
        "block_id": block_id,
        "selected_block_key": target_id,
        "page_number": int(current_candidate.get("page_number") or 0),
        "resolved_via": "human_decision",
    })
    graph["source_fusion_repairs"] = repairs
    graph.pop(_SOURCE_REVIEW_KEY, None)

    remaining = _source_review_block_ids(graph, canonical=canonical)
    retained = [
        copy.deepcopy(issue)
        for issue in graph.get("issues") or []
        if isinstance(issue, dict)
        and issue.get("code") not in {
            "converter_semantic_markup_requires_pdf_reconciliation",
            "semantic_source_rich_text",
            "semantic_source_hash_mismatch",
            "unresolved_source_override_markup",
            "invalid_source_override_hash",
        }
    ]
    if remaining:
        retained.append({
            "code": "converter_semantic_markup_requires_pdf_reconciliation",
            "severity": "error",
            "block_ids": remaining,
            "message": (
                "Additional converter blocks still require an explicit "
                "verified-PDF source decision."
            ),
        })
        graph["status"] = "review_required"
    else:
        retained.append({
            "code": "converter_semantic_markup_repaired_from_verified_pdf",
            "severity": "warning",
            "block_ids": [block_id],
            "message": (
                "The user selected an already-verified original-PDF block; "
                "source text was rendered deterministically."
            ),
        })
        graph["status"] = "ready"
        graph["source_review_resolution"] = {
            "version": _SOURCE_REVIEW_VERSION,
            "decision_id": str(state.get("decision_id") or ""),
            "context_hash": str(state.get("context_hash") or ""),
            "pdf_sha256": expected_pdf_sha256,
        }
        graph[_DOWNSTREAM_INVALIDATION_KEY] = True
    graph["issues"] = retained
    semantic_source = render_semantic_source(graph, canonical)
    graph["semantic_source_sha256"] = _sha256_text(semantic_source)
    errors = validate_graph(
        graph,
        canonical=canonical,
        semantic_source=semantic_source,
        allow_unresolved_source_anomalies=bool(remaining),
    )
    if errors:
        graph["status"] = "failed"
        graph["issues"].extend(errors)
    return graph


def _apply_verified_numbered_topic_patch(
    graph: dict[str, Any],
    state: dict[str, Any],
    *,
    canonical: dict[str, Any],
    page_bundle: dict[str, Any] | None,
    target_id: str,
) -> dict[str, Any]:
    """Re-derive and apply one selected canonical topic patch API-free."""

    expected_pdf_sha256 = str(state.get("pdf_sha256") or "")
    current_pdf_sha256 = str((page_bundle or {}).get("pdf_sha256") or "")
    if expected_pdf_sha256 and current_pdf_sha256 != expected_pdf_sha256:
        raise ValueError(
            "the verified PDF identity changed after the source patch was shown"
        )
    preview = _numbered_topic_patch_preview(
        graph,
        canonical=canonical,
        page_bundle=page_bundle,
    )
    if preview is None:
        raise ValueError(
            "the numbered-topic source patch is stale or no longer required"
        )
    repaired, current_patch = preview
    saved_patch = state.get("source_patch")
    if not isinstance(saved_patch, dict):
        raise ValueError("the saved source patch preview is unavailable")
    sealed_fields = (
        "version", "kind", "target", "verified", "raw_source_mutated",
        "source_contract_hash", "semantic_context_hash", "before_sha256",
        "after_sha256", "patch_hash", "target_id", "before", "after",
        "operations",
    )
    if any(
        current_patch.get(field) != saved_patch.get(field)
        for field in sealed_fields
    ):
        raise ValueError(
            "the canonical source patch changed after the decision was shown"
        )
    if (
        not target_id
        or target_id != str(current_patch.get("target_id") or "")
    ):
        raise ValueError(
            "the selected source patch is not the current verified candidate"
        )
    resolution = copy.deepcopy(
        repaired.get("numbered_topic_patch_resolution") or {}
    )
    resolution.update({
        "human_decision_id": str(state.get("decision_id") or ""),
        "human_decision_context_hash": str(state.get("context_hash") or ""),
        "resolved_via": "agent_or_human_decision",
    })
    repaired["numbered_topic_patch_resolution"] = resolution
    repaired.pop(_SOURCE_REVIEW_KEY, None)
    return repaired


def _source_review_graph_or_raise(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
    page_bundle: dict[str, Any] | None,
    source_path: Path | None,
) -> dict[str, Any]:
    """Resolve one saved answer or pause with one bounded diagnostic."""

    if graph.get("status") == "ready":
        return graph
    numbered_topic_review = any(
        isinstance(row, dict)
        and row.get("severity") == "error"
        and row.get("code") == "numbered_main_topic_coverage"
        for row in graph.get("issues") or []
    )
    unresolved = _source_review_block_ids(graph, canonical=canonical)
    block_id = unresolved[0] if unresolved else ""
    state = graph.get(_SOURCE_REVIEW_KEY)
    if numbered_topic_review:
        if (
            not isinstance(state, dict)
            or state.get("version") != _SOURCE_REVIEW_VERSION
            or state.get("review_kind") != "canonical_topic_patch"
        ):
            state = _build_numbered_topic_patch_review_state(
                graph,
                canonical=canonical,
                page_bundle=page_bundle,
            )
            graph[_SOURCE_REVIEW_KEY] = state
    elif (
        not isinstance(state, dict)
        or state.get("version") != _SOURCE_REVIEW_VERSION
        or str((state.get("item") or {}).get("block_id") or "") != block_id
    ):
        state = _build_source_review_state(
            graph,
            canonical=canonical,
            page_bundle=page_bundle,
            source_path=source_path,
            block_id=block_id,
        )
        graph[_SOURCE_REVIEW_KEY] = state

    decision_id = str(state.get("decision_id") or "")
    context_hash = str(state.get("context_hash") or "")
    resolution = _human_source_resolution(
        decision_id=decision_id,
        context_hash=context_hash,
    )
    if resolution is None:
        raise semantic_recovery.HumanDecisionRequired(
            _source_review_pending(
                graph, state, canonical=canonical))

    choice = str(resolution.get("choice") or "")
    target_id = str(
        resolution.get("target_id")
        or resolution.get("target_concept_id")
        or ""
    )
    if choice == "accept_recommended":
        target_id = str(state.get("recommended_target_id") or target_id)
    if choice in {"accept_recommended", "select_candidate"}:
        if state.get("review_kind") == "canonical_topic_patch":
            updated = _apply_verified_numbered_topic_patch(
                graph,
                state,
                canonical=canonical,
                page_bundle=page_bundle,
                target_id=target_id,
            )
        else:
            updated = _apply_human_source_candidate(
                graph,
                state,
                canonical=canonical,
                page_bundle=page_bundle,
                target_id=target_id,
            )
        if updated.get("status") != "ready":
            return _source_review_graph_or_raise(
                updated,
                canonical=canonical,
                page_bundle=page_bundle,
                source_path=source_path,
            )
        return updated

    if choice == "carry_forward":
        # Nothing is applied. The source stays exactly as uploaded and the
        # review travels into the release flagged, so the run still produces a
        # map the reviewer can read and correct.
        state["status"] = "carried"
        state["carried_reason"] = (
            "No automatic route remained for this source review; the source "
            "was left unchanged and the run continued."
        )
        graph[_SOURCE_REVIEW_KEY] = state
        return graph

    if choice == "replace_source":
        raise ValueError(
            "Source replacement is required before generation can continue. "
            "Replace or correct the uploaded file and convert it again; Aegis "
            "will not mutate the source automatically."
        )

    instruction = str(resolution.get("instruction") or "").strip()
    if choice != "custom_instruction" or not instruction:
        raise ValueError("source-review custom instruction is empty")
    if state.get("custom_interpretation_used"):
        raise ValueError(
            "this source-review instruction has already used its one bounded "
            "interpretation")
    interpretation: dict[str, Any] = {}
    if semantic_api_enabled():
        try:
            interpretation = _interpret_custom_source_instruction_via_openai(
                {
                    "diagnosis": state.get("diagnosis"),
                    "decision_question": state.get("decision_question"),
                    "item": state.get("item"),
                    "candidates": [
                        {
                            "target_id": row.get("target_id"),
                            "page_number": row.get("page_number"),
                            "kind": row.get("kind"),
                            "visible_text": row.get("visible_text"),
                        }
                        for row in state.get("candidates") or []
                        if isinstance(row, dict)
                    ],
                },
                instruction=instruction,
                source_path=source_path,
            )
        except Exception as exc:
            raise RuntimeError(
                "The source-review provider could not interpret the custom "
                "instruction. No automatic retry ran and the custom option "
                "was not consumed; resume explicitly after provider access "
                "is restored."
            ) from exc
    selected = str(interpretation.get("selected_target_id") or "")
    candidate_ids = {
        str(row.get("target_id") or "")
        for row in state.get("candidates") or []
        if isinstance(row, dict)
    }
    if (
        interpretation.get("decision") == "use_verified_page_block"
        and selected in candidate_ids
    ):
        if state.get("review_kind") == "canonical_topic_patch":
            updated = _apply_verified_numbered_topic_patch(
                graph,
                state,
                canonical=canonical,
                page_bundle=page_bundle,
                target_id=selected,
            )
        else:
            updated = _apply_human_source_candidate(
                graph,
                state,
                canonical=canonical,
                page_bundle=page_bundle,
                target_id=selected,
            )
        if updated.get("status") != "ready":
            return _source_review_graph_or_raise(
                updated,
                canonical=canonical,
                page_bundle=page_bundle,
                source_path=source_path,
            )
        return updated

    revision = {
        "custom_interpretation_used": True,
        "prior_decision_id": decision_id,
        "instruction_sha256": _sha256_text(instruction),
        "decision": str(interpretation.get("decision") or ""),
        "reason": str(interpretation.get("reason") or "")[:2_000],
    }
    if state.get("review_kind") == "canonical_topic_patch":
        next_state = _build_numbered_topic_patch_review_state(
            graph,
            canonical=canonical,
            page_bundle=page_bundle,
            revision=revision,
        )
    else:
        next_state = _build_source_review_state(
            graph,
            canonical=canonical,
            page_bundle=page_bundle,
            source_path=source_path,
            block_id=block_id,
            revision=revision,
            allow_diagnostic_call=False,
        )
    next_state["custom_interpretation_used"] = True
    next_state["recommended_target_id"] = ""
    next_state["diagnosis"] = str(
        interpretation.get("reason")
        or state.get("diagnosis")
        or ""
    )[:8_000]
    next_state["decision_question"] = str(
        interpretation.get("question")
        or (
            (
                "Choose the verified canonical-source patch directly."
                if next_state.get("review_kind") == "canonical_topic_patch"
                else "Choose one verified PDF evidence block directly."
            )
            if next_state.get("candidates")
            else "Replace or correct the source file before resuming."
        )
    )[:8_000]
    graph[_SOURCE_REVIEW_KEY] = next_state
    raise semantic_recovery.HumanDecisionRequired(
        _source_review_pending(
            graph, next_state, canonical=canonical))


def _numbered_main_topic_mismatches(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return structured numbered-topic drift without parsing error prose."""

    bindings = _numbered_main_binding_rows(canonical)
    if len(bindings) < 2:
        return []
    topics = [
        row for row in graph.get("topics") or [] if isinstance(row, dict)
    ]
    topics_by_section: dict[str, list[dict[str, Any]]] = {}
    for topic in topics:
        section_id = str(topic.get("section_id") or "")
        if section_id:
            topics_by_section.setdefault(section_id, []).append(topic)
    binding_counts: dict[str, int] = {}
    for binding in bindings:
        section_id = str(binding.get("resolved_section_id") or "")
        if section_id:
            binding_counts[section_id] = binding_counts.get(section_id, 0) + 1
    graph_blocks_by_id = {
        str(row.get("block_id") or ""): row
        for row in graph.get("blocks") or []
        if isinstance(row, dict) and str(row.get("block_id") or "")
    }
    mismatches: list[dict[str, Any]] = []
    for binding in bindings:
        section_id = str(binding.get("resolved_section_id") or "")
        expected_title = str(binding.get("expected_title") or "").strip()
        expected_number = str(binding.get("number") or "")
        section_topics = topics_by_section.get(section_id, [])
        topic = section_topics[0] if len(section_topics) == 1 else None
        reasons: list[str] = []
        if not section_id or binding_counts.get(section_id, 0) != 1:
            reasons.append("non_unique_canonical_section_binding")
        if len(section_topics) != 1:
            reasons.append("missing_or_duplicate_topic_for_section")
        if isinstance(topic, dict) and (
            _semantic_title_key(topic.get("title"))
            != _semantic_title_key(expected_title)
        ):
            reasons.append("changed_title")
        if isinstance(topic, dict) and (
            str(topic.get("structural_number") or "") != expected_number
        ):
            reasons.append("changed_structural_number")
        heading_block = graph_blocks_by_id.get(
            str(binding.get("block_id") or "")
        )
        adjudicated_heading = (
            str(binding.get("heading_origin") or "")
            == "adjudicated_pdf"
        )
        if adjudicated_heading:
            if not _verified_adjudicated_heading_binding(
                binding,
                canonical=canonical,
            ):
                reasons.append("invalid_adjudicated_heading_provenance")
        else:
            if not isinstance(heading_block, dict) or (
                str(heading_block.get("section_id") or "") != section_id
            ):
                reasons.append("heading_block_section_mismatch")
            if isinstance(topic, dict) and (
                not isinstance(heading_block, dict)
                or str(heading_block.get("topic_id") or "")
                != str(topic.get("topic_id") or "")
            ):
                reasons.append("heading_block_topic_mismatch")
        if (
            isinstance(topic, dict)
            and not reasons
            and _semantic_title_key(topic.get("title"))
            == _semantic_title_key(expected_title)
            and str(topic.get("structural_number") or "")
            == expected_number
        ):
            continue
        actual = topic or next(
            (
                row for row in topics
                if (
                    _semantic_title_key(row.get("title"))
                    == _semantic_title_key(expected_title)
                    or str(row.get("structural_number") or "")
                    == expected_number
                )
            ),
            {},
        )
        mismatches.append({
            **copy.deepcopy(binding),
            "actual_topic_id": str(actual.get("topic_id") or ""),
            "actual_section_id": str(actual.get("section_id") or ""),
            "actual_title": str(actual.get("title") or ""),
            "actual_structural_number": str(
                actual.get("structural_number") or ""
            ),
            "reasons": reasons or ["missing_numbered_topic"],
        })
    return mismatches


def validate_graph(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
    semantic_source: str,
    allow_unresolved_source_anomalies: bool = False,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    topic_ids = [str(row.get("topic_id") or "") for row in graph.get("topics") or []]
    subtopic_ids = [str(row.get("subtopic_id") or "") for row in graph.get("subtopics") or []]
    block_ids = [str(row.get("block_id") or "") for row in graph.get("blocks") or []]
    task_ids = [str(row.get("task_id") or "") for row in graph.get("tasks") or []]
    qids = [str(row.get("qid") or "") for row in graph.get("tasks") or []]
    for label, values in (
        ("topic", topic_ids), ("subtopic", subtopic_ids), ("block", block_ids),
        ("task", task_ids), ("qid", qids),
    ):
        nonempty = [value for value in values if value]
        if len(nonempty) != len(set(nonempty)):
            errors.append({
                "severity": "error", "code": f"duplicate_{label}_identity",
                "message": f"The semantic graph contains duplicate {label} identities.",
            })
    allowed_topics = set(topic_ids)
    allowed_subtopics = set(subtopic_ids)
    allowed_blocks = {
        str(row.get("block_id") or "") for row in canonical.get("blocks") or []
        if isinstance(row, dict)
    }
    allowed_tasks = {
        str(row.get("task_id") or "") for row in canonical.get("tasks") or []
        if isinstance(row, dict)
    }
    allowed_qids = {
        str(row.get("qid") or "") for row in canonical.get("tasks") or []
        if isinstance(row, dict)
    }
    # Two or more numbered main headings are unambiguous chapter-topology
    # evidence. Every one must retain its own graph topic. A stale block-to-
    # section association is rebound from exact canonical evidence above; this
    # guard therefore reports the expected and actual identities separately.
    numbered_mismatches = _numbered_main_topic_mismatches(
        graph, canonical=canonical)
    if numbered_mismatches:
        errors.append({
            "severity": "error",
            "code": "numbered_main_topic_coverage",
            "mismatches": numbered_mismatches,
            "message": (
                "Semantic graph omitted or changed numbered main topic(s): "
                + "; ".join(
                    f"{row.get('number')} {row.get('expected_title')}"
                    f" [expected section {row.get('resolved_section_id')}; "
                    f"actual section {row.get('actual_section_id') or 'missing'}; "
                    f"reasons {','.join(row.get('reasons') or [])}]"
                    for row in numbered_mismatches
                )
            ),
        })
    for row in graph.get("blocks") or []:
        if row.get("block_id") not in allowed_blocks:
            errors.append({"severity": "error", "code": "unknown_block_id", "message": str(row.get("block_id"))})
        if row.get("topic_id") not in allowed_topics:
            errors.append({"severity": "error", "code": "unknown_block_topic", "message": str(row.get("block_id"))})
        if row.get("subtopic_id") and row.get("subtopic_id") not in allowed_subtopics:
            errors.append({"severity": "error", "code": "unknown_block_subtopic", "message": str(row.get("block_id"))})
        override = row.get("source_override")
        if isinstance(override, dict):
            resolved = str(override.get("resolved_text") or "")
            if not resolved or override.get("resolved_sha256") != _sha256_text(resolved):
                errors.append({
                    "severity": "error",
                    "code": "invalid_source_override_hash",
                    "message": str(row.get("block_id")),
                })
            if _SUSPICIOUS_MARKUP_RE.search(resolved):
                errors.append({
                    "severity": "error",
                    "code": "unresolved_source_override_markup",
                    "message": str(row.get("block_id")),
                })
    for row in graph.get("tasks") or []:
        if row.get("task_id") not in allowed_tasks or row.get("qid") not in allowed_qids:
            errors.append({"severity": "error", "code": "unknown_task_identity", "message": str(row.get("task_id"))})
        if row.get("topic_id") not in allowed_topics:
            errors.append({"severity": "error", "code": "unknown_task_topic", "message": str(row.get("task_id"))})
    canonical_block_order = [
        str(row.get("block_id") or "") for row in sorted(
            [item for item in canonical.get("blocks") or [] if isinstance(item, dict)],
            key=lambda item: int(item.get("order") or 0),
        )
    ]
    if block_ids != canonical_block_order:
        errors.append({
            "severity": "error", "code": "block_order_drift",
            "message": "Semantic graph block order differs from ACSD source order.",
        })
    canonical_qids = [
        str(row.get("qid") or "") for row in sorted(
            [item for item in canonical.get("tasks") or [] if isinstance(item, dict)],
            key=lambda item: int(item.get("order") or 0),
        )
    ]
    if qids != canonical_qids:
        errors.append({
            "severity": "error", "code": "task_order_drift",
            "message": "Semantic graph QID order differs from the ACSD task ledger.",
        })
    if graph.get("source_contract_hash") != source_contract_hash(canonical):
        errors.append({
            "severity": "error", "code": "stale_source_contract_hash",
            "message": "Semantic graph was compiled from a stale ACSD contract.",
        })
    if graph.get("semantic_context_hash") != semantic_context_hash(
        graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
    ):
        errors.append({
            "severity": "error", "code": "invalid_semantic_context_hash",
            "message": "Semantic graph academic context does not match its context hash.",
        })
    if graph.get("semantic_source_sha256") and graph.get("semantic_source_sha256") != _sha256_text(semantic_source):
        errors.append({
            "severity": "error", "code": "semantic_source_hash_mismatch",
            "message": "Rendered semantic source does not match its graph hash.",
        })
    issues = kr.rich_text_issues(semantic_source)
    if issues and not (
        allow_unresolved_source_anomalies
        and graph.get("status") == "review_required"
        and any(
            issue.get("code")
            == "converter_semantic_markup_requires_pdf_reconciliation"
            for issue in graph.get("issues") or []
        )
        and set(issues).issubset({"raw_latex"})
    ):
        affected_blocks = _rich_text_issue_block_ids(
            graph, canonical=canonical)
        errors.append({
            "severity": "error", "code": "semantic_source_rich_text",
            "block_ids": affected_blocks,
            "message": (
                "Phase 3 semantic source has non-canonical rich text: "
                + ", ".join(issues)
            ),
        })
    return errors


def _topic_alias_map(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    aliases: dict[str, dict[str, Any]] = {}
    topics = {
        str(row.get("topic_id") or ""): row for row in graph.get("topics") or []
        if isinstance(row, dict)
    }
    for topic in topics.values():
        for value in (
            topic.get("title"), topic.get("display_title"),
            f"{topic.get('structural_number')} {topic.get('title')}" if topic.get("structural_number") else "",
        ):
            key = _normal(value)
            if key:
                aliases[key] = topic
    for subtopic in graph.get("subtopics") or []:
        if not isinstance(subtopic, dict):
            continue
        parent = topics.get(str(subtopic.get("topic_id") or ""))
        if not parent:
            continue
        for value in (
            subtopic.get("title"), subtopic.get("display_title"),
            f"{subtopic.get('structural_number')} {subtopic.get('title')}" if subtopic.get("structural_number") else "",
        ):
            key = _normal(value)
            if key:
                aliases[key] = parent
    return aliases


def canonical_topic_for_text(graph: dict[str, Any], value: object) -> dict[str, Any] | None:
    key = _normal(value)
    aliases = _topic_alias_map(graph)
    if key in aliases:
        return aliases[key]
    stripped = _NUMBER_PREFIX_RE.sub(lambda match: match.group("title") or "", str(value or ""))
    return aliases.get(_normal(stripped))


_LEAF_QID_RE = re.compile(r"^(?P<parent>.+)\.(?P<leaf>\d+)$")


def _graph_task_for_qid(
    task_by_qid: dict[str, dict[str, Any]],
    qid: object,
    *,
    parent_qid: object = "",
) -> dict[str, Any] | None:
    """Resolve an inventory leaf to its immutable canonical parent task.

    Phase 2.1 deliberately keeps the original 26 task objects and materializes
    independently answerable children only in the production inventory.  The
    semantic graph therefore owns the parent task ID while the leaf owns its
    own route and Example identity.
    """
    value = str(qid or "").strip()
    direct = task_by_qid.get(value)
    if direct is not None:
        return direct
    parent = str(parent_qid or "").strip()
    if parent and parent in task_by_qid:
        return task_by_qid[parent]
    matched = _LEAF_QID_RE.match(value)
    return task_by_qid.get(matched.group("parent")) if matched else None


_BOUNDARY_ARTIFACT_GAP = 200


def _subtopic_split_from_task_figure(
    graph: dict[str, Any],
    task: dict[str, Any],
    subtopic_id: str,
) -> str:
    """Return a figure the subtopic boundary separated from its own task.

    A two-column textbook page linearizes into one stream, so an Activity box
    belonging to the figures above it can land just after the next subheading.
    The offsets and the boundary are both faithful; the subtopic is simply not
    evidence of what the task is about.

    Only a figure that *abuts* the boundary counts.  A task legitimately
    referring back to a figure from an earlier subtopic sits far from it, and
    must keep its own subtopic.
    """

    figure_ids = {
        str(value) for value in task.get("figure_ids") or [] if str(value)
    }
    if not figure_ids or not subtopic_id:
        return ""
    subtopic = next(
        (
            row for row in graph.get("subtopics") or []
            if isinstance(row, dict)
            and str(row.get("subtopic_id") or "") == subtopic_id
        ),
        None,
    )
    if not isinstance(subtopic, dict):
        return ""
    try:
        boundary = int(subtopic.get("source_start") or 0)
    except (TypeError, ValueError):
        return ""
    for figure in graph.get("figures") or []:
        if not isinstance(figure, dict):
            continue
        figure_id = str(figure.get("figure_id") or "")
        if figure_id not in figure_ids:
            continue
        try:
            end = int(figure.get("source_end") or 0)
        except (TypeError, ValueError):
            continue
        if 0 < boundary - end <= _BOUNDARY_ARTIFACT_GAP:
            return figure_id
    return ""


def annotate_inventory(inventory: dict[str, Any], graph: dict[str, Any] | None = None) -> dict[str, Any]:
    graph = graph or active_graph()
    if not isinstance(graph, dict):
        return inventory
    from . import generation

    out = copy.deepcopy(inventory or {})
    task_by_qid = {
        str(row.get("qid") or ""): row for row in graph.get("tasks") or []
        if isinstance(row, dict) and str(row.get("qid") or "")
    }
    topic_by_id = {
        str(row.get("topic_id") or ""): row for row in graph.get("topics") or []
        if isinstance(row, dict)
    }
    subtopic_by_id = {
        str(row.get("subtopic_id") or ""): row for row in graph.get("subtopics") or []
        if isinstance(row, dict)
    }
    for item in out.get("items") or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("qid") or "")
        task = _graph_task_for_qid(
            task_by_qid,
            qid,
            parent_qid=item.get("parent_qid") or item.get("_acsd_parent_qid"),
        )
        if not task:
            continue
        assigned_topic = None
        physical_task_topic_id = str(task.get("topic_id") or "")
        if (
            item.get("_chapter_wide_task")
            or item.get("_topic_scope") == "chapter"
            or task.get("chapter_wide")
        ):
            assigned_topic = canonical_topic_for_text(graph, item.get("topic_hint"))
        semantic_topic_id = str(task.get("topic_id") or "")
        semantic_subtopic_id = str(task.get("subtopic_id") or "")
        if assigned_topic is not None:
            # The reviewed leaf route is an annotation, not a graph mutation.
            # Several chapter-wide leaves can share one parent task while
            # legitimately belonging to different source topics.
            semantic_topic_id = str(assigned_topic.get("topic_id") or "")
            semantic_subtopic_id = ""
        else:
            split_figure = _subtopic_split_from_task_figure(
                graph, task, semantic_subtopic_id
            )
            if split_figure:
                # Clear rather than reassign.  Which subtopic the task belongs
                # to is a semantic judgement this deterministic layer may not
                # make, but asserting one the task's own figure contradicts is
                # worse than asserting none: the main topic still carries
                # ownership, and evidence scoping falls back to topic level.
                progress.log(
                    f"Task {qid} sits just after the "
                    f"{semantic_subtopic_id!r} boundary while its figure "
                    f"{split_figure} sits just before it; the subtopic label "
                    "is a page-layout artifact and was cleared. Its main "
                    "topic and source provenance are unchanged.",
                    level="warning",
                )
                item["_semantic_subtopic_boundary_artifact"] = split_figure
                semantic_subtopic_id = ""
        source_location_topic_id = str(
            item.get("source_location_topic_id")
            or physical_task_topic_id
            or ""
        )
        # Once recorded, physical source provenance is immutable. A certified
        # owner may change semantic routing but can never rewrite this field.
        item["source_location_topic_id"] = source_location_topic_id
        source_topic = topic_by_id.get(source_location_topic_id, {})
        if source_topic.get("title"):
            item["source_location_topic_title"] = str(
                source_topic.get("title") or "")
        placement = generation._certified_type_case_placement_contract(
            item.get("_type_case_placement_contract")
        )
        if (
            placement is None
            and isinstance(item.get("_type_case_placement_contract"), dict)
            and str(item["_type_case_placement_contract"].get("version") or "")
            == str(generation._TYPE_CASE_PLACEMENT_CONTRACT_VERSION)
        ):
            raise RuntimeError(
                "type_case_owner_uncertified: inventory QID placement v2 is "
                "present but its certificate is invalid"
            )
        if placement is not None:
            semantic_topic_id = str(placement.get("owner_topic_id") or "")
            if semantic_topic_id != source_location_topic_id:
                semantic_subtopic_id = ""
            item["owner_topic_id"] = semantic_topic_id
            owner_topic = topic_by_id.get(semantic_topic_id, {})
            item["owner_topic_title"] = str(
                placement.get("owner_topic_title")
                or owner_topic.get("title")
                or ""
            )
        topic = topic_by_id.get(semantic_topic_id, {})
        subtopic = subtopic_by_id.get(semantic_subtopic_id, {})
        item["_semantic_topic_id"] = semantic_topic_id
        item["_semantic_subtopic_id"] = semantic_subtopic_id
        item["_semantic_graph_contract"] = graph.get("source_contract_hash")
        item["_semantic_source_task_id"] = str(task.get("task_id") or "")
        # The visible hint remains for legacy prompts, but structural identity is
        # now the graph ID and always uses the canonical main-topic title.
        if topic.get("title") and placement is None:
            item["topic_hint"] = str(topic["title"])
        if subtopic.get("title"):
            item["_semantic_subtopic_title"] = str(subtopic["title"])
        else:
            item.pop("_semantic_subtopic_title", None)
    return out


def _qid_scope(
    graph: dict[str, Any],
    qids: Iterable[object],
    *,
    topic_hint: object = "",
) -> tuple[list[str], list[str]]:
    task_by_qid = {
        str(row.get("qid") or ""): row for row in graph.get("tasks") or []
        if isinstance(row, dict) and str(row.get("qid") or "")
    }
    topic_ids: list[str] = []
    subtopic_ids: list[str] = []
    for raw_qid in qids:
        task = _graph_task_for_qid(task_by_qid, raw_qid)
        if not task:
            continue
        topic_id = str(task.get("topic_id") or "")
        subtopic_id = str(task.get("subtopic_id") or "")
        # Type/Case scope reads the graph task directly, so the page-layout
        # guard that ``annotate_inventory`` applies has to be applied here too.
        # Otherwise a unit built from this task keeps asserting the subtopic
        # its own figure contradicts.
        if subtopic_id and _subtopic_split_from_task_figure(
            graph, task, subtopic_id
        ):
            subtopic_id = ""
        if topic_id and topic_id not in topic_ids:
            topic_ids.append(topic_id)
        if subtopic_id and subtopic_id not in subtopic_ids:
            subtopic_ids.append(subtopic_id)
    routed_topic = canonical_topic_for_text(graph, topic_hint)
    if routed_topic is not None:
        routed_topic_id = str(routed_topic.get("topic_id") or "")
        if routed_topic_id:
            # A Case route is authoritative for a chapter-wide inventory leaf.
            # A subtopic inherited from the physical parent is not valid after
            # moving that leaf to a different semantic main topic.
            if topic_ids != [routed_topic_id]:
                subtopic_ids = []
            topic_ids = [routed_topic_id]
    return topic_ids, subtopic_ids


def annotate_mined_types(
    mined_types: dict[str, Any], graph: dict[str, Any] | None = None
) -> dict[str, Any]:
    graph = graph or active_graph()
    if not isinstance(graph, dict):
        return mined_types
    from . import generation

    out = copy.deepcopy(mined_types or {})
    topic_by_id = {
        str(row.get("topic_id") or ""): row for row in graph.get("topics") or []
        if isinstance(row, dict)
    }
    for mtype in out.get("types") or []:
        if not isinstance(mtype, dict):
            continue
        case_topic_ids: list[str] = []
        case_subtopic_ids: list[str] = []
        case_owned_qids: set[str] = set()
        certified_case_count = 0
        cases = [
            case for case in mtype.get("case_prompts") or []
            if isinstance(case, dict)
        ]
        for case in cases:
            case_qids = generation._assignment_case_qids(case)
            case_owned_qids.update(case_qids)
            placement = generation._certified_type_case_placement_contract(
                case.get("_type_case_placement_contract")
            )
            if placement is not None:
                certified_case_count += 1
                owner_topic_id = str(
                    placement.get("owner_topic_id") or "")
                if owner_topic_id not in topic_by_id:
                    raise RuntimeError(
                        "type_case_owner_uncertified: Case uses unknown owner "
                        f"topic {owner_topic_id or '<empty>'}"
                    )
                case_topics = [owner_topic_id]
                case_subtopics: list[str] = []
                case["owner_topic_id"] = owner_topic_id
                case["owner_topic_title"] = str(
                    placement.get("owner_topic_title")
                    or topic_by_id[owner_topic_id].get("title")
                    or ""
                )
                case["source_location_topic_ids"] = list(
                    placement.get("source_location_topic_ids") or [])
                case["topic_match_hint"] = case["owner_topic_title"]
            else:
                declared = isinstance(
                    case.get("_type_case_placement_contract"), dict
                ) and str(
                    case["_type_case_placement_contract"].get("version") or ""
                ) == str(generation._TYPE_CASE_PLACEMENT_CONTRACT_VERSION)
                if declared:
                    raise RuntimeError(
                        "type_case_owner_uncertified: Case placement v2 is "
                        "present but its certificate is invalid"
                    )
                raw_topics, _raw_subtopics = _qid_scope(graph, case_qids)
                explicit_cross_topic = (
                    str(case.get("placement_scope") or "").strip().lower()
                    == "cross_topic_synthesis"
                    and len(raw_topics) > 1
                )
                case_topics, case_subtopics = _qid_scope(
                    graph,
                    case_qids,
                    topic_hint=(
                        "" if explicit_cross_topic
                        else case.get("topic_match_hint") or ""
                    ),
                )
            case["_semantic_topic_ids"] = case_topics
            case["_semantic_subtopic_ids"] = case_subtopics
            for topic_id in case_topics:
                if topic_id not in case_topic_ids:
                    case_topic_ids.append(topic_id)
            for subtopic_id in case_subtopics:
                if subtopic_id not in case_subtopic_ids:
                    case_subtopic_ids.append(subtopic_id)
            if len(case_topics) == 1:
                case["_semantic_topic_id"] = case_topics[0]
                case.pop("_semantic_scope", None)
            elif len(case_topics) > 1:
                # Cross-topic synthesis belongs to one integrated Case. Merely
                # reusing an operator Type across separately routed Cases does
                # not turn those Cases into synthesis.
                case.pop("_semantic_topic_id", None)
                case["placement_scope"] = "cross_topic_synthesis"
                case["_semantic_scope"] = "cross_topic_synthesis"

        unclaimed_qids = [
            qid for qid in generation._type_source_qids(mtype)
            if qid not in case_owned_qids
        ]
        fallback_topics, fallback_subtopics = _qid_scope(
            graph,
            unclaimed_qids,
            topic_hint=(mtype.get("topic_match_hint") or "") if not cases else "",
        )
        for topic_id in fallback_topics:
            if topic_id not in case_topic_ids:
                case_topic_ids.append(topic_id)
        for subtopic_id in fallback_subtopics:
            if subtopic_id not in case_subtopic_ids:
                case_subtopic_ids.append(subtopic_id)
        topic_ids = case_topic_ids
        subtopic_ids = case_subtopic_ids
        mtype["_semantic_topic_ids"] = topic_ids
        mtype["_semantic_subtopic_ids"] = subtopic_ids
        mtype["_semantic_graph_contract"] = graph.get("source_contract_hash")
        if len(topic_ids) == 1:
            topic = topic_by_id.get(topic_ids[0], {})
            if topic.get("title"):
                mtype["topic_match_hint"] = str(topic["title"])
            if cases and certified_case_count == len(cases):
                mtype["owner_topic_id"] = topic_ids[0]
                mtype["owner_topic_ids"] = list(topic_ids)
            mtype["_semantic_scope"] = "topic"
        elif len(topic_ids) > 1:
            if cases and certified_case_count == len(cases):
                mtype["owner_topic_id"] = ""
                mtype["owner_topic_ids"] = list(topic_ids)
                mtype["topic_match_hint"] = ""
            mtype["_semantic_scope"] = "multi_topic_reuse"
            integrated_cross_topic_case = bool(
                len(cases) == 1
                and len(cases[0].get("_semantic_topic_ids") or []) > 1
            )
            mtype["placement_scope"] = (
                "cross_topic_synthesis"
                if integrated_cross_topic_case else "normal"
            )
    return out


def annotate_assignment_units(
    units: list[dict[str, Any]], graph: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    graph = graph or active_graph()
    if not isinstance(graph, dict):
        return units
    from . import generation

    topic_by_id = {
        str(row.get("topic_id") or ""): row for row in graph.get("topics") or []
        if isinstance(row, dict)
    }
    out = copy.deepcopy(units)
    for unit in out:
        placement = generation._certified_type_case_placement_contract(
            unit.get("_type_case_placement_contract")
        )
        if placement is not None:
            owner_topic_id = str(placement.get("owner_topic_id") or "")
            topic = topic_by_id.get(owner_topic_id)
            if topic is None:
                raise RuntimeError(
                    "type_case_owner_uncertified: assignment unit uses unknown "
                    f"owner topic {owner_topic_id or '<empty>'}"
                )
            unit["owner_topic_id"] = owner_topic_id
            unit["owner_topic_title"] = str(
                placement.get("owner_topic_title")
                or topic.get("title")
                or ""
            )
            unit["source_location_topic_ids"] = list(
                placement.get("source_location_topic_ids") or [])
            unit["_semantic_topic_ids"] = [owner_topic_id]
            unit["_semantic_topic_id"] = owner_topic_id
            unit["_semantic_subtopic_ids"] = []
            unit["topic_match_hint"] = unit["owner_topic_title"]
            if unit.get("placement_scope") == "cross_topic_synthesis":
                unit["placement_scope"] = "mixed_synthesis"
            continue
        declared = isinstance(
            unit.get("_type_case_placement_contract"), dict
        ) and str(
            unit["_type_case_placement_contract"].get("version") or ""
        ) == str(generation._TYPE_CASE_PLACEMENT_CONTRACT_VERSION)
        if declared:
            raise RuntimeError(
                "type_case_owner_uncertified: assignment-unit placement v2 "
                "is present but its certificate is invalid"
            )
        qids = list(unit.get("source_question_ids") or [])
        raw_topic_ids, _raw_subtopic_ids = _qid_scope(graph, qids)
        explicit_cross_topic = (
            str(unit.get("placement_scope") or "").strip().lower()
            == "cross_topic_synthesis"
            and len(raw_topic_ids) > 1
        )
        topic_ids, subtopic_ids = _qid_scope(
            graph,
            qids,
            topic_hint=(
                "" if explicit_cross_topic
                else unit.get("topic_match_hint") or ""
            ),
        )
        unit["_semantic_topic_ids"] = topic_ids
        unit["_semantic_subtopic_ids"] = subtopic_ids
        if len(topic_ids) == 1:
            topic = topic_by_id.get(topic_ids[0], {})
            unit["_semantic_topic_id"] = topic_ids[0]
            if topic.get("title"):
                unit["topic_match_hint"] = str(topic["title"])
            if unit.get("placement_scope") == "cross_topic_synthesis":
                unit["placement_scope"] = "mixed_synthesis"
        elif len(topic_ids) > 1:
            unit.pop("_semantic_topic_id", None)
            unit["placement_scope"] = "cross_topic_synthesis"
            first = topic_by_id.get(topic_ids[0], {})
            if first.get("title"):
                unit["topic_match_hint"] = str(first["title"])
    return out


def _missing_host_schema(
    assignment_unit_ids: list[str], source_block_ids: list[str]
) -> dict[str, Any]:
    return {
        "name": "aegis_phase3_missing_type_host_concepts",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "concepts": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": max(1, len(assignment_unit_ids)),
                    "items": {
                        "type": "object",
                        "properties": {
                            "concept_title": {"type": "string"},
                            "parent_concept": {"type": "string"},
                            "description": {"type": "string"},
                            "achieving_mastery": {"type": "string"},
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "source_block_ids": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "string",
                                    "enum": source_block_ids,
                                },
                            },
                            "assignment_unit_ids": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "string",
                                    "enum": assignment_unit_ids,
                                },
                            },
                        },
                        "required": [
                            "concept_title",
                            "parent_concept",
                            "description",
                            "achieving_mastery",
                            "keywords",
                            "source_block_ids",
                            "assignment_unit_ids",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["concepts"],
            "additionalProperties": False,
        },
    }


def _missing_host_critic_schema() -> dict[str, Any]:
    return {
        "name": "aegis_phase3_missing_type_host_critic",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["verified", "rejected"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["verdict", "confidence", "issues"],
            "additionalProperties": False,
        },
    }


def _create_missing_hosts_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    unit_ids = [
        str(row.get("assignment_unit_id") or "")
        for row in payload.get("assignment_units") or []
    ]
    block_ids = [
        str(row.get("block_id") or "")
        for row in payload.get("source_blocks") or []
    ]
    system = (
        "You are the Aegis missing-concept reconciler. A canonical textbook "
        "topic contains source-grounded Type assignment units but the restored "
        "checkpoint has no normal concept in that topic. Create the smallest "
        "pedagogically coherent set of normal concepts needed to host every "
        "assignment unit exactly once. Preserve grade and subject level. Use "
        "only supplied source_block_ids and assignment_unit_ids. Do not create "
        "a culmination, activity, question, person-only label, or unsupported "
        "fact. Description and mastery must be specific enough to teach and "
        "assess the source method or idea."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_missing_host_schema(unit_ids, block_ids),
        purpose="concept_mapping",
        max_tokens=max(5000, min(24000, len(unit_ids) * 900)),
    )


def _critic_missing_hosts_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    system = (
        "You are the independent Aegis concept-topology critic. Verify that "
        "the proposed missing concepts are necessary, source-grounded, "
        "non-duplicative, grade-appropriate, and collectively cover every "
        "assignment unit exactly once without over-merging distinct methods. "
        "Reject any unsupported, generic, culmination-like, or question-shaped "
        "concept. Do not rewrite the proposal."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_missing_host_critic_schema(),
        purpose="concept_mapping",
        max_tokens=4000,
    )


def ensure_type_scope_hosts(
    records: list[dict[str, Any]],
    *,
    mined_types: dict[str, Any],
    graph: dict[str, Any] | None = None,
    canonical: dict[str, Any] | None = None,
    provider: AnomalyProvider | None = None,
    critic: AnomalyCritic | None = None,
) -> list[dict[str, Any]]:
    """Create source-grounded normal hosts before the legacy allocator gate.

    This is the fail-soft turnover for an 81% legacy checkpoint: verified
    concepts, inventory, and Types survive, while only missing topic topology is
    reconciled before allocation. Heading text is never used as identity.
    """
    graph = graph or active_graph()
    if not isinstance(graph, dict):
        return records
    canonical = canonical or (active_session() or {}).get("canonical") or {}
    from . import concept_refiner as cr
    from . import generation

    out = canonicalize_record_topics(records, graph)
    annotated_types = annotate_mined_types(mined_types, graph)
    units = annotate_assignment_units(
        generation._expand_mined_types_to_assignment_units(
            list(annotated_types.get("types") or [])
        ),
        graph,
    )
    topic_by_id = {
        str(row.get("topic_id") or ""): row
        for row in graph.get("topics") or [] if isinstance(row, dict)
    }
    normal_topics = {
        str(row.get("_semantic_topic_id") or "")
        for row in out
        if not cr.is_culmination(row.get("concept_title", ""))
    }
    units_by_topic: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        if unit.get("is_activity"):
            continue
        topic_ids = [str(value) for value in unit.get("_semantic_topic_ids") or []]
        if len(topic_ids) != 1:
            continue
        topic_id = topic_ids[0]
        if topic_id and topic_id not in normal_topics:
            units_by_topic.setdefault(topic_id, []).append(unit)
    if not units_by_topic:
        return out

    canonical_blocks = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or [] if isinstance(row, dict)
    }
    graph_blocks = [
        row for row in graph.get("blocks") or [] if isinstance(row, dict)
    ]
    provider = provider or (
        _create_missing_hosts_via_openai if semantic_api_enabled() else None
    )
    critic = critic or (
        _critic_missing_hosts_via_openai if semantic_api_enabled() else None
    )
    if provider is None or critic is None:
        missing_titles = [
            str((topic_by_id.get(topic_id) or {}).get("title") or topic_id)
            for topic_id in units_by_topic
        ]
        raise RuntimeError(
            "Phase 3 Type-scope preflight requires semantic reconciliation for "
            + ", ".join(missing_titles)
        )

    for topic_id in [
        str(row.get("topic_id") or "") for row in graph.get("topics") or []
        if str(row.get("topic_id") or "") in units_by_topic
    ]:
        topic = topic_by_id[topic_id]
        topic_units = units_by_topic[topic_id]
        source_blocks: list[dict[str, Any]] = []
        for block in graph_blocks:
            if block.get("topic_id") != topic_id or block.get("kind") in {"layout", "heading"}:
                continue
            source = canonical_blocks.get(str(block.get("block_id") or ""), {})
            visible = _clean_public_text(_graph_block_text(block, source))
            if visible:
                source_blocks.append({
                    "block_id": str(block.get("block_id") or ""),
                    "subtopic_id": str(block.get("subtopic_id") or ""),
                    "kind": str(block.get("kind") or ""),
                    "text": visible[:2200],
                })
        if not source_blocks:
            raise RuntimeError(
                f"Phase 3 cannot create a Type host for {topic.get('title')!r}: "
                "the canonical topic has no source blocks"
            )
        unit_payload: list[dict[str, Any]] = []
        unit_ids: list[str] = []
        for index, unit in enumerate(topic_units, start=1):
            unit_id = str(unit.get("type_id") or f"UNIT-{index:04d}")
            if unit_id in unit_ids:
                unit_id = f"{unit_id}--{index:03d}"
            unit_ids.append(unit_id)
            unit_payload.append({
                "assignment_unit_id": unit_id,
                "type_title": str(unit.get("type_title") or unit.get("title") or ""),
                "type_description": str(unit.get("type_description") or unit.get("description") or ""),
                "source_question_ids": [
                    str(value) for value in unit.get("source_question_ids") or []
                ],
                "case_title": str(unit.get("case_title") or ""),
                "case_definition": str(unit.get("case_definition") or ""),
            })
        payload = {
            "metadata": copy.deepcopy(graph.get("metadata") or {}),
            "topic": copy.deepcopy(topic),
            "assignment_units": unit_payload,
            "source_blocks": source_blocks,
        }
        proposal = provider(copy.deepcopy(payload))
        concepts = proposal.get("concepts") if isinstance(proposal, dict) else None
        if not isinstance(concepts, list) or not concepts:
            raise RuntimeError(
                f"Phase 3 missing-host reconciliation returned no concepts for {topic.get('title')!r}"
            )
        allowed_units = set(unit_ids)
        allowed_blocks = {row["block_id"] for row in source_blocks}
        covered: list[str] = []
        titles: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for concept in concepts:
            if not isinstance(concept, dict):
                raise RuntimeError("Phase 3 missing-host reconciliation returned a non-object concept")
            title = _plain_title(concept.get("concept_title"))
            title_key = _normal(title)
            block_ids = [str(value) for value in concept.get("source_block_ids") or []]
            assigned = [str(value) for value in concept.get("assignment_unit_ids") or []]
            if (
                not title
                or title_key in titles
                or not block_ids
                or any(value not in allowed_blocks for value in block_ids)
                or not assigned
                or any(value not in allowed_units for value in assigned)
            ):
                raise RuntimeError("Phase 3 missing-host proposal violates its bounded ID contract")
            titles.add(title_key)
            covered.extend(assigned)
            description = _SPACE_RE.sub(" ", str(concept.get("description") or "")).strip()
            mastery = _SPACE_RE.sub(" ", str(concept.get("achieving_mastery") or "")).strip()
            parent = _plain_title(concept.get("parent_concept")) or str(topic.get("title") or "Core Concepts")
            if not description or not mastery:
                raise RuntimeError("Phase 3 missing-host proposal omitted pedagogical content")
            normalized.append({
                "topic": str(topic.get("title") or ""),
                "parent_concept": parent,
                "concept_title": title,
                "concept_details": (
                    f"Description: {description}\n"
                    f"Achieving Mastery: {mastery}"
                ),
                "keywords": ", ".join(
                    str(value).strip()
                    for value in concept.get("keywords") or []
                    if str(value).strip()
                ),
                "_semantic_topic_id": topic_id,
                "_semantic_graph_contract": graph.get("source_contract_hash"),
                "_source_block_ids": block_ids,
                "_semantic_subtopic_ids": sorted({
                    str(block.get("subtopic_id") or "")
                    for block in graph_blocks
                    if str(block.get("block_id") or "") in block_ids
                    and str(block.get("subtopic_id") or "")
                }),
                "_source_grounding_contract": "api-created-missing-type-host",
                "_phase3_assignment_unit_ids": assigned,
            })
        if sorted(covered) != sorted(allowed_units) or len(covered) != len(set(covered)):
            raise RuntimeError(
                "Phase 3 missing-host proposals did not cover every assignment unit exactly once"
            )
        review = critic({
            **copy.deepcopy(payload),
            "proposed_concepts": copy.deepcopy(normalized),
        })
        if (
            not isinstance(review, dict)
            or review.get("verdict") != "verified"
            or not confidence_policy.accepts(review.get("confidence"))
            or bool(review.get("issues"))
        ):
            raise RuntimeError(
                f"Phase 3 independent critic rejected missing Type hosts for {topic.get('title')!r}"
            )
        # Insert immediately before that topic's culmination, or before the next
        # topic, preserving canonical chapter order.
        insertion = len(out)
        topic_order = int(topic.get("order") or 0)
        for index, row in enumerate(out):
            row_topic = topic_by_id.get(str(row.get("_semantic_topic_id") or ""), {})
            if (
                str(row.get("_semantic_topic_id") or "") == topic_id
                and cr.is_culmination(row.get("concept_title", ""))
            ):
                insertion = index
                break
            if int(row_topic.get("order") or 0) > topic_order:
                insertion = index
                break
        out[insertion:insertion] = normalized
        normal_topics.add(topic_id)
        progress.log(
            f"Phase 3 Type-scope preflight created {len(normalized)} "
            f"source-grounded concept host(s) for {topic.get('title')!r}.",
            level="success",
        )
    return out


def _record_topic_schema(
    concept_ids: list[str], topic_ids: list[str]
) -> dict[str, Any]:
    return {
        "name": "aegis_phase3_concept_topic_resolution",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "assignments": {
                    "type": "array",
                    "minItems": len(concept_ids),
                    "maxItems": len(concept_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "concept_id": {"type": "string", "enum": concept_ids},
                            "topic_id": {"type": "string", "enum": topic_ids},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "reason": {"type": "string"},
                        },
                        "required": ["concept_id", "topic_id", "confidence", "reason"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["assignments"],
            "additionalProperties": False,
        },
    }


def _record_topic_critic_schema() -> dict[str, Any]:
    return {
        "name": "aegis_phase3_concept_topic_resolution_critic",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["verified", "rejected"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["verdict", "confidence", "issues"],
            "additionalProperties": False,
        },
    }


def _resolve_record_topics_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids = [str(row["concept_id"]) for row in payload.get("concepts") or []]
    topic_ids = [str(row["topic_id"]) for row in payload.get("topics") or []]
    system = (
        "Assign each supplied pedagogical concept to exactly one canonical textbook "
        "main-topic ID. Use the concept title, parent, description, source order, "
        "and the supplied topic excerpts. Visible generated topic labels are hints, "
        "not identities. Use only supplied opaque concept and topic IDs. Do not "
        "rewrite, merge, omit, or create concepts."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_record_topic_schema(concept_ids, topic_ids),
        purpose="concept_mapping",
        max_tokens=max(4000, min(16000, len(concept_ids) * 180)),
    )


def _critic_record_topics_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    system = (
        "Independently verify every proposed concept-to-topic assignment against "
        "the supplied canonical textbook topic excerpts and concept descriptions. "
        "Reject assignments based only on heading-string similarity, source-order "
        "convenience, or a broad culmination when a specific topic is supported. "
        "Do not rewrite or reassign anything."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_record_topic_critic_schema(),
        purpose="concept_mapping",
        max_tokens=4000,
    )


def canonicalize_record_topics(
    records: list[dict[str, Any]],
    graph: dict[str, Any] | None = None,
    *,
    provider: TopicResolutionProvider | None = None,
    critic: TopicResolutionCritic | None = None,
) -> list[dict[str, Any]]:
    """Resolve concept topics by stable graph ID without silent string guessing."""
    graph = graph or active_graph()
    if not isinstance(graph, dict):
        return records
    out = copy.deepcopy(records)
    topics = [row for row in graph.get("topics") or [] if isinstance(row, dict)]
    topic_by_id = {
        str(row.get("topic_id") or ""): row
        for row in topics if str(row.get("topic_id") or "")
    }
    unresolved: list[int] = []
    for index, record in enumerate(out):
        existing_id = str(record.get("_semantic_topic_id") or "")
        topic = topic_by_id.get(existing_id)
        if topic is None:
            topic = canonical_topic_for_text(graph, record.get("topic"))
        if topic is None:
            unresolved.append(index)
            continue
        record["topic"] = str(topic.get("title") or record.get("topic") or "")
        record["_semantic_topic_id"] = str(topic.get("topic_id") or "")
        record["_semantic_graph_contract"] = graph.get("source_contract_hash")

    if unresolved and topics:
        provider = provider or (
            _resolve_record_topics_via_openai if semantic_api_enabled() else None
        )
        critic = critic or (
            _critic_record_topics_via_openai if semantic_api_enabled() else None
        )
        if provider is not None and critic is not None:
            excerpts = {
                str(row.get("topic") or ""): str(row.get("excerpt") or "")
                for row in graph_topic_excerpts(graph)
            }
            concepts: list[dict[str, Any]] = []
            concept_index: dict[str, int] = {}
            for local, index in enumerate(unresolved, start=1):
                concept_id = f"CONCEPT-TOPIC-{local:04d}"
                concept_index[concept_id] = index
                record = out[index]
                concepts.append({
                    "concept_id": concept_id,
                    "source_order": index + 1,
                    "generated_topic_hint": str(record.get("topic") or ""),
                    "concept_title": str(record.get("concept_title") or ""),
                    "parent_concept": str(record.get("parent_concept") or ""),
                    "description": str(record.get("concept_details") or "")[:2400],
                })
            topic_payload = [{
                "topic_id": str(topic.get("topic_id") or ""),
                "order": int(topic.get("order") or 0),
                "title": str(topic.get("title") or ""),
                "excerpt": excerpts.get(str(topic.get("title") or ""), "")[:5000],
            } for topic in topics]
            payload = {
                "metadata": copy.deepcopy(graph.get("metadata") or {}),
                "topics": topic_payload,
                "concepts": concepts,
            }
            response = provider(copy.deepcopy(payload))
            rows = response.get("assignments") if isinstance(response, dict) else None
            if not isinstance(rows, list):
                raise ValueError("Phase 3 concept-topic resolver returned no assignments")
            seen: set[str] = set()
            proposals: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("Phase 3 concept-topic resolver returned a non-object row")
                concept_id = str(row.get("concept_id") or "")
                topic_id = str(row.get("topic_id") or "")
                confidence = float(row.get("confidence") or 0.0)
                if (
                    concept_id not in concept_index
                    or concept_id in seen
                    or topic_id not in topic_by_id
                    or not confidence_policy.accepts(confidence)
                ):
                    raise ValueError("Phase 3 concept-topic resolver violated its bounded ID contract")
                seen.add(concept_id)
                proposals.append({
                    "concept_id": concept_id,
                    "topic_id": topic_id,
                    "confidence": confidence,
                    "reason": str(row.get("reason") or ""),
                })
            if seen != set(concept_index):
                raise ValueError("Phase 3 concept-topic resolver omitted concept IDs")
            review = critic({
                **copy.deepcopy(payload),
                "proposed_assignments": copy.deepcopy(proposals),
            })
            if (
                not isinstance(review, dict)
                or review.get("verdict") != "verified"
                or not confidence_policy.accepts(review.get("confidence"))
                or bool(review.get("issues"))
            ):
                raise ValueError("Phase 3 concept-topic assignments failed independent verification")
            for proposal in proposals:
                index = concept_index[proposal["concept_id"]]
                topic = topic_by_id[proposal["topic_id"]]
                out[index]["topic"] = str(topic.get("title") or "")
                out[index]["_semantic_topic_id"] = proposal["topic_id"]
                out[index]["_semantic_graph_contract"] = graph.get("source_contract_hash")
                out[index]["_semantic_topic_confidence"] = proposal["confidence"]
                out[index]["_semantic_topic_contract"] = "api-verified-topic-id"
        else:
            # Explicit offline/test mode retains deterministic source progression.
            for index in unresolved:
                previous = next(
                    (
                        topic_by_id.get(str(out[prior].get("_semantic_topic_id") or ""))
                        for prior in range(index - 1, -1, -1)
                        if str(out[prior].get("_semantic_topic_id") or "") in topic_by_id
                    ),
                    None,
                )
                topic = previous or topics[0]
                out[index]["topic"] = str(topic.get("title") or out[index].get("topic") or "")
                out[index]["_semantic_topic_id"] = str(topic.get("topic_id") or "")
                out[index]["_semantic_graph_contract"] = graph.get("source_contract_hash")
                out[index]["_semantic_topic_contract"] = "offline-source-order-fallback"
    return out

def _grounding_schema(concept_ids: list[str], block_ids: list[str]) -> dict[str, Any]:
    return {
        "name": "phase3_concept_source_grounding",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "concepts": {
                    "type": "array",
                    "minItems": len(concept_ids),
                    "maxItems": len(concept_ids),
                    "items": {
                        "type": "object",
                        "properties": {
                            "concept_id": {"type": "string", "enum": concept_ids},
                            "source_block_ids": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"type": "string", "enum": block_ids},
                            },
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "reason": {"type": "string"},
                        },
                        "required": ["concept_id", "source_block_ids", "confidence", "reason"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["concepts"],
            "additionalProperties": False,
        },
    }


def _ground_records_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    concept_ids = [str(row["concept_id"]) for row in payload.get("concepts") or []]
    block_ids = [str(row["block_id"]) for row in payload.get("source_blocks") or []]
    system = (
        "Ground every pedagogical concept to the smallest sufficient set of "
        "source blocks from its already-fixed canonical topic. Use only supplied "
        "opaque IDs. Do not rewrite concepts or source text. A concept must be "
        "supported by visible textbook evidence, not merely topical similarity."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_grounding_schema(concept_ids, block_ids),
        purpose="concept_mapping",
        max_tokens=max(4000, min(20000, len(concept_ids) * 220)),
    )


def _grounding_critic_schema() -> dict[str, Any]:
    return {
        "name": "phase3_concept_source_grounding_critic",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["verified", "rejected"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["verdict", "confidence", "issues"],
            "additionalProperties": False,
        },
    }


def _critic_ground_records_via_openai(payload: dict[str, Any]) -> dict[str, Any]:
    system = (
        "Independently verify that every proposed concept-to-source-block grounding "
        "is visibly supported, topic-bounded, minimally sufficient, and not based "
        "on broad topical similarity. Reject omitted concepts, unrelated blocks, "
        "or confidence unsupported by the supplied source. Do not rewrite anything."
    )
    return phase22._openai_multimodal_json(
        system=system,
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        pages=[],
        response_schema=_grounding_critic_schema(),
        purpose="concept_mapping",
        max_tokens=4000,
    )


def ground_concepts(
    records: list[dict[str, Any]],
    *,
    graph: dict[str, Any] | None = None,
    canonical: dict[str, Any] | None = None,
    provider: GroundingProvider | None = None,
    critic: GroundingCritic | None = None,
) -> list[dict[str, Any]]:
    graph = graph or active_graph()
    if not isinstance(graph, dict) or not records:
        return records
    out = canonicalize_record_topics(records, graph)
    canonical = canonical or (active_session() or {}).get("canonical") or {}
    canonical_blocks = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or [] if isinstance(row, dict)
    }
    graph_blocks = [row for row in graph.get("blocks") or [] if isinstance(row, dict)]
    topic_by_id = {
        str(row.get("topic_id") or ""): row for row in graph.get("topics") or []
        if isinstance(row, dict)
    }
    provider = provider or (_ground_records_via_openai if semantic_api_enabled() else None)
    critic = critic or (
        _critic_ground_records_via_openai if semantic_api_enabled() else None
    )
    for topic_id, topic in topic_by_id.items():
        indices = [
            index for index, row in enumerate(out)
            if str(row.get("_semantic_topic_id") or "") == topic_id
        ]
        if not indices:
            continue
        candidates = [
            block for block in graph_blocks
            if block.get("topic_id") == topic_id
            and block.get("kind") not in {"layout", "heading"}
        ]
        candidate_payload = []
        for block in candidates:
            source = canonical_blocks.get(str(block.get("block_id") or ""), {})
            text = _clean_public_text(_graph_block_text(block, source))
            if not text:
                continue
            candidate_payload.append({
                "block_id": block["block_id"],
                "kind": block.get("kind"),
                "subtopic_id": block.get("subtopic_id") or "",
                "text": text[:1800],
            })
        if not candidate_payload:
            continue
        concepts_payload = []
        concept_id_by_index: dict[str, int] = {}
        for local, index in enumerate(indices, start=1):
            concept_id = f"CONCEPT-GROUND-{local:04d}"
            concept_id_by_index[concept_id] = index
            concepts_payload.append({
                "concept_id": concept_id,
                "concept_title": str(out[index].get("concept_title") or ""),
                "parent_concept": str(out[index].get("parent_concept") or ""),
                "description": str(out[index].get("concept_details") or "")[:2200],
            })
        if provider is None:
            # Deterministic bounded fallback: ground to all source blocks in the
            # topic rather than inventing semantic precision.
            for index in indices:
                block_ids = [row["block_id"] for row in candidate_payload]
                out[index]["_source_block_ids"] = block_ids
                out[index]["_semantic_subtopic_ids"] = sorted({
                    str(block.get("subtopic_id") or "")
                    for block in candidates
                    if block.get("subtopic_id")
                })
                out[index]["_source_grounding_contract"] = "topic-bounded-deterministic"
            continue
        payload = {
            "topic": topic,
            "concepts": concepts_payload,
            "source_blocks": candidate_payload,
        }
        response = provider(payload)
        rows = response.get("concepts") if isinstance(response, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Phase 3 concept grounding returned no concepts array")
        if critic is None:
            raise ValueError("Phase 3 concept grounding requires an independent critic")
        seen: set[str] = set()
        allowed_blocks = {str(row["block_id"]) for row in candidate_payload}
        proposals: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Phase 3 concept grounding returned a non-object row")
            concept_id = str(row.get("concept_id") or "")
            confidence = float(row.get("confidence") or 0.0)
            if concept_id not in concept_id_by_index or concept_id in seen:
                raise ValueError("Phase 3 concept grounding changed concept identity")
            seen.add(concept_id)
            block_ids = [str(value) for value in row.get("source_block_ids") or []]
            if (
                not block_ids
                or any(value not in allowed_blocks for value in block_ids)
                or not confidence_policy.accepts(confidence)
            ):
                raise ValueError("Phase 3 concept grounding used an invalid or uncertain source block")
            proposals.append({
                "concept_id": concept_id,
                "source_block_ids": block_ids,
                "confidence": confidence,
                "reason": str(row.get("reason") or ""),
            })
        if seen != set(concept_id_by_index):
            raise ValueError("Phase 3 concept grounding omitted concept IDs")
        review = critic({
            **copy.deepcopy(payload),
            "proposed_grounding": copy.deepcopy(proposals),
        })
        if (
            not isinstance(review, dict)
            or review.get("verdict") != "verified"
            or not confidence_policy.accepts(review.get("confidence"))
            or bool(review.get("issues"))
        ):
            raise ValueError("Phase 3 concept grounding failed independent verification")
        for proposal in proposals:
            index = concept_id_by_index[proposal["concept_id"]]
            block_ids = proposal["source_block_ids"]
            out[index]["_source_block_ids"] = block_ids
            out[index]["_semantic_subtopic_ids"] = sorted({
                str(next(
                    (block.get("subtopic_id") for block in candidates if block.get("block_id") == block_id),
                    "",
                ) or "")
                for block_id in block_ids
            } - {""})
            out[index]["_source_grounding_contract"] = "api-verified-source-block-ids"
            out[index]["_source_grounding_confidence"] = proposal["confidence"]
    return out


def graph_topic_headings(graph: dict[str, Any] | None = None) -> list[str]:
    graph = graph or active_graph()
    if not isinstance(graph, dict):
        return []
    return [str(row.get("title") or "") for row in graph.get("topics") or [] if str(row.get("title") or "")]


def graph_topic_excerpts(
    graph: dict[str, Any] | None = None,
    canonical: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    graph = graph or active_graph()
    if not isinstance(graph, dict):
        return []
    canonical = canonical or (active_session() or {}).get("canonical") or {}
    blocks = {
        str(row.get("block_id") or ""): row
        for row in canonical.get("blocks") or [] if isinstance(row, dict)
    }
    out: list[dict[str, str]] = []
    for topic in graph.get("topics") or []:
        topic_id = str(topic.get("topic_id") or "")
        pieces: list[str] = []
        for block in graph.get("blocks") or []:
            if not isinstance(block, dict) or block.get("topic_id") != topic_id:
                continue
            source = blocks.get(str(block.get("block_id") or ""), {})
            text = _clean_public_text(_graph_block_text(block, source))
            if text:
                pieces.append(text)
        out.append({"topic": str(topic.get("title") or ""), "excerpt": "\n\n".join(pieces)})
    return out


def load_page_evidence(
    source_path: Path,
    artifact_dir: Path,
    *,
    job_id: int | None = None,
) -> dict[str, Any] | None:
    if not vision_enabled() or source_path.suffix.lower() != ".pdf":
        return None
    artifact_path = Path(artifact_dir) / VISION_ACSD_FILENAME
    pdf_sha = page_acsd._pdf_sha256(source_path)
    bundle: dict[str, Any] | None = None
    if artifact_path.exists():
        try:
            cached = json.loads(artifact_path.read_text(encoding="utf-8"))
            # The schema gate uses the producer's constant: a hardcoded
            # version here previously never matched what the extractor
            # stamped, so this cache was dead and every Phase 3 rebuild
            # re-entered the full PDF-to-ACSD lane.
            if (
                isinstance(cached, dict)
                and cached.get("pdf_sha256") == pdf_sha
                and cached.get("compiler_version") == page_acsd.FALLBACK_COMPILER
                and cached.get("schema_version")
                == page_acsd.PAGE_ACSD_SCHEMA_VERSION
            ):
                bundle = cached
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            bundle = None
    if bundle is None:
        progress.step("Canonical source — Phase 3 full PDF evidence fusion", value=0.01)
        progress.log(
            "Phase 3 is extracting and independently verifying every original PDF "
            "page as a parallel semantic evidence channel.",
            level="warning",
        )
        bundle = page_acsd.extract_pdf_to_page_acsd(source_path)
    # Figure assets are needed not only by the full fallback but also when a
    # verified PDF table cell repairs converter-only semantic markup.
    if job_id is not None:
        needs_assets = any(
            block.get("kind") == "figure" and not block.get("asset_url")
            for page in bundle.get("pages") or [] if isinstance(page, dict)
            for block in page.get("blocks") or [] if isinstance(block, dict)
        )
        if needs_assets:
            page_acsd.materialize_visual_assets(
                source_path,
                bundle,
                job_id=int(job_id),
                artifact_dir=Path(artifact_dir),
            )
    _atomic_write(artifact_path, _json_text(bundle))
    return bundle


def write_artifacts(
    directory: Path,
    *,
    graph: dict[str, Any],
    report: dict[str, Any],
    semantic_source: str,
    page_bundle: dict[str, Any] | None = None,
) -> None:
    directory = Path(directory)
    _atomic_write(directory / GRAPH_FILENAME, _json_text(graph))
    _atomic_write(directory / GRAPH_REPORT_FILENAME, _json_text(report))
    _atomic_write(directory / SEMANTIC_SOURCE_FILENAME, semantic_source)
    if page_bundle is not None:
        _atomic_write(directory / VISION_ACSD_FILENAME, _json_text(page_bundle))


def _updated_graph_report(
    graph: dict[str, Any],
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mirror mutable review status without changing source identities."""

    out = copy.deepcopy(report or {})
    out.update({
        "schema_name": f"{SCHEMA_NAME} Report",
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "semantic_confidence_policy": confidence_policy.cache_identity(),
        "phase": PHASE,
        "status": graph.get("status"),
        "source_contract_hash": graph.get("source_contract_hash"),
        "semantic_context_hash": graph.get("semantic_context_hash"),
        "source_sha256": graph.get("source_sha256"),
        "semantic_source_sha256": graph.get("semantic_source_sha256"),
        "classification_mode": graph.get("classification_mode"),
        "issues": copy.deepcopy(graph.get("issues") or []),
    })
    summary = dict(out.get("summary") or {})
    summary.update({
        "topics": len(graph.get("topics") or []),
        "subtopics": len(graph.get("subtopics") or []),
        "sections": len(graph.get("sections") or []),
        "blocks": len(graph.get("blocks") or []),
        "tasks": len(graph.get("tasks") or []),
        "figures": len(graph.get("figures") or []),
        "images": len(graph.get("images") or []),
        "math_spans": len(graph.get("math") or []),
        "vision_pages": int(
            (graph.get("vision_evidence") or {}).get("page_count") or 0),
        "errors": sum(
            1 for row in graph.get("issues") or []
            if isinstance(row, dict) and row.get("severity") == "error"
        ),
        "warnings": sum(
            1 for row in graph.get("issues") or []
            if isinstance(row, dict) and row.get("severity") == "warning"
        ),
        "source_fusion_repairs": len(
            graph.get("source_fusion_repairs") or []),
    })
    out["summary"] = summary
    return out


def load_graph(directory: Path, canonical: dict[str, Any]) -> dict[str, Any] | None:
    path = Path(directory) / GRAPH_FILENAME
    if not path.exists():
        return None
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(graph, dict):
        return None
    if (
        graph.get("schema_name") != SCHEMA_NAME
        or graph.get("schema_version") != SCHEMA_VERSION
        or graph.get("compiler_version") != COMPILER_VERSION
        or graph.get("semantic_confidence_policy")
        != confidence_policy.cache_identity()
        or graph.get("phase") != PHASE
    ):
        return None
    if graph.get("source_contract_hash") != source_contract_hash(canonical):
        return None
    return graph


def _migrate_machine_metadata_semantic_source(
    value: Any,
    *,
    canonical: dict[str, Any],
) -> tuple[dict[str, Any], str] | None:
    """Upgrade a graph sealed by the pre-sanitization renderer.

    The stored hash must match either the legacy renderer byte-for-byte or the
    already-sanitized renderer. The two renderings must differ, which proves
    that this path is changing only allowlisted machine comments rather than
    accepting arbitrary source drift.
    """

    if not isinstance(value, dict):
        return None
    graph = copy.deepcopy(value)
    legacy_source = render_semantic_source(
        graph,
        canonical,
        strip_machine_metadata=False,
        preserve_markdown_code=False,
    )
    metadata_only_source = render_semantic_source(
        graph,
        canonical,
        strip_machine_metadata=True,
        preserve_markdown_code=False,
    )
    semantic_source = render_semantic_source(graph, canonical)
    if (
        legacy_source == semantic_source
        or metadata_only_source != semantic_source
    ):
        return None
    stored_hash = str(graph.get("semantic_source_sha256") or "")
    legacy_hash = _sha256_text(legacy_source)
    semantic_hash = _sha256_text(semantic_source)
    if stored_hash != legacy_hash:
        return None

    graph["semantic_source_sha256"] = semantic_hash
    graph[_MACHINE_METADATA_MIGRATION_KEY] = {
        "version": _MACHINE_METADATA_MIGRATION_VERSION,
        "legacy_semantic_source_sha256": legacy_hash,
        "semantic_source_sha256": semantic_hash,
    }
    graph.pop(_SOURCE_REVIEW_KEY, None)
    recomputed_codes = {
        "semantic_source_hash_mismatch",
        "semantic_source_rich_text",
    }
    retained = [
        copy.deepcopy(issue)
        for issue in graph.get("issues") or []
        if isinstance(issue, dict)
        and str(issue.get("code") or "") not in recomputed_codes
    ]
    has_retained_error = any(
        issue.get("severity") == "error" for issue in retained
    )
    graph["issues"] = retained
    graph["status"] = "review_required" if has_retained_error else "ready"
    errors = validate_graph(
        graph,
        canonical=canonical,
        semantic_source=semantic_source,
        allow_unresolved_source_anomalies=has_retained_error,
    )
    if errors:
        graph["status"] = "failed"
        graph["issues"].extend(errors)
    return graph, semantic_source


def _migrate_adjudicated_heading_validation_false_positive(
    value: Any,
    *,
    canonical: dict[str, Any],
    semantic_source: str,
) -> dict[str, Any] | None:
    """Revalidate one API-verified graph rejected only for a pseudo block.

    Compiler v2 briefly required the intentionally non-materialized Phase 2.2
    heading ID to appear in ``graph.blocks``.  This migration is deliberately
    narrower than a general issue reset: every saved mismatch must describe
    only those impossible physical-block checks, the current canonical repair
    ledger must still verify the adjudicated heading, and the entire graph must
    pass the current validator before its stale error is cleared.
    """

    if not isinstance(value, dict) or (
        value.get("status") not in {"failed", "review_required"}
        or value.get("classification_mode")
        != "api_classified_and_verified"
        or value.get(_SOURCE_REVIEW_KEY) is not None
        or value.get("semantic_source_sha256")
        != _sha256_text(semantic_source)
    ):
        return None
    error_issues = [
        row
        for row in value.get("issues") or []
        if isinstance(row, dict) and row.get("severity") == "error"
    ]
    if not error_issues or {
        str(row.get("code") or "") for row in error_issues
    } != {"numbered_main_topic_coverage"}:
        return None
    mismatches: list[Mapping[str, Any]] = []
    for issue in error_issues:
        raw_mismatches = issue.get("mismatches")
        if (
            not isinstance(raw_mismatches, list)
            or not raw_mismatches
            or any(
                not isinstance(mismatch, Mapping)
                for mismatch in raw_mismatches
            )
        ):
            return None
        mismatches.extend(raw_mismatches)
    current_bindings = _numbered_main_binding_rows(canonical)
    allowed_stale_reasons = {
        "heading_block_section_mismatch",
        "heading_block_topic_mismatch",
    }
    migrated_bindings: list[dict[str, Any]] = []
    for mismatch in mismatches:
        binding = next(
            (
                row
                for row in current_bindings
                if str(row.get("number") or "")
                == str(mismatch.get("number") or "")
                and str(row.get("resolved_section_id") or "")
                == str(mismatch.get("resolved_section_id") or "")
                and str(row.get("block_id") or "")
                == str(mismatch.get("block_id") or "")
            ),
            None,
        )
        reasons = {
            str(reason) for reason in mismatch.get("reasons") or [] if reason
        }
        if (
            not isinstance(binding, Mapping)
            or not reasons
            or not reasons.issubset(allowed_stale_reasons)
            or not _verified_adjudicated_heading_binding(
                binding,
                canonical=canonical,
            )
            or str(mismatch.get("actual_section_id") or "")
            != str(binding.get("resolved_section_id") or "")
            or _semantic_title_key(mismatch.get("actual_title"))
            != _semantic_title_key(binding.get("expected_title"))
            or str(mismatch.get("actual_structural_number") or "")
            != str(binding.get("number") or "")
        ):
            return None
        migrated_bindings.append(copy.deepcopy(dict(binding)))
    graph = copy.deepcopy(value)
    if validate_graph(
        graph,
        canonical=canonical,
        semantic_source=semantic_source,
    ):
        return None
    retained = [
        copy.deepcopy(issue)
        for issue in graph.get("issues") or []
        if isinstance(issue, dict) and issue.get("severity") != "error"
    ]
    migration_material = {
        "version": _ADJUDICATED_HEADING_MIGRATION_VERSION,
        "source_contract_hash": str(graph.get("source_contract_hash") or ""),
        "semantic_context_hash": str(
            graph.get("semantic_context_hash") or ""
        ),
        "semantic_source_sha256": _sha256_text(semantic_source),
        "bindings": migrated_bindings,
    }
    migration_hash = _sha256_json(migration_material)
    graph[_ADJUDICATED_HEADING_MIGRATION_KEY] = {
        **migration_material,
        "migration_sha256": migration_hash,
    }
    graph["issues"] = [
        *retained,
        {
            "severity": "warning",
            "code": "adjudicated_heading_validation_migrated",
            "message": (
                "Revalidated the API-verified graph against the sealed Phase "
                "2.2 PDF heading; no source, hierarchy, or topic identity was "
                "changed."
            ),
            "migration_sha256": migration_hash,
        },
    ]
    graph["status"] = "ready"
    return graph


def machine_metadata_migration_seal_valid(
    value: Any,
    *,
    canonical: dict[str, Any],
) -> bool:
    """Verify that a ready graph changed only by metadata sanitization."""

    if not isinstance(value, dict) or value.get("status") != "ready":
        return False
    if (
        value.get(_DOWNSTREAM_INVALIDATION_KEY)
        or value.get("source_review_resolution")
        or _has_human_selected_source_overrides(value)
    ):
        return False
    seal = value.get(_MACHINE_METADATA_MIGRATION_KEY)
    if (
        not isinstance(seal, dict)
        or seal.get("version") != _MACHINE_METADATA_MIGRATION_VERSION
    ):
        return False
    legacy_source = render_semantic_source(
        value,
        canonical,
        strip_machine_metadata=False,
        preserve_markdown_code=False,
    )
    metadata_only_source = render_semantic_source(
        value,
        canonical,
        strip_machine_metadata=True,
        preserve_markdown_code=False,
    )
    semantic_source = render_semantic_source(value, canonical)
    if (
        legacy_source == semantic_source
        or metadata_only_source != semantic_source
    ):
        return False
    legacy_hash = _sha256_text(legacy_source)
    semantic_hash = _sha256_text(semantic_source)
    return bool(
        seal.get("legacy_semantic_source_sha256") == legacy_hash
        and seal.get("semantic_source_sha256") == semantic_hash
        and value.get("semantic_source_sha256") == semantic_hash
        and not validate_graph(
            value,
            canonical=canonical,
            semantic_source=semantic_source,
        )
    )


def machine_metadata_source_review_is_obsolete(
    value: Any,
    *,
    canonical: dict[str, Any],
    decision_id: str,
    context_hash: str,
) -> bool:
    """Prove that one saved source pause was caused only by metadata."""

    if not isinstance(value, dict) or value.get("status") == "ready":
        return False
    review = value.get(_SOURCE_REVIEW_KEY)
    if not isinstance(review, dict):
        return False
    if (
        str(review.get("decision_id") or "") != str(decision_id or "")
        or str(review.get("context_hash") or "") != str(context_hash or "")
    ):
        return False
    error_codes = {
        str(issue.get("code") or "")
        for issue in value.get("issues") or []
        if isinstance(issue, dict) and issue.get("severity") == "error"
    }
    if error_codes != {"semantic_source_rich_text"}:
        return False
    migrated = _migrate_machine_metadata_semantic_source(
        value,
        canonical=canonical,
    )
    return bool(migrated and migrated[0].get("status") == "ready")


def _compatible_source_review_graph(
    value: Any,
    *,
    canonical: dict[str, Any],
    metadata: dict[str, Any],
    page_bundle: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate a paused or corrected graph against the current source."""

    if not isinstance(value, dict):
        return None
    graph = copy.deepcopy(value)
    if (
        graph.get("schema_name") != SCHEMA_NAME
        or graph.get("schema_version") != SCHEMA_VERSION
        or graph.get("compiler_version") != COMPILER_VERSION
        or graph.get("phase") != PHASE
        or graph.get("semantic_confidence_policy")
        != confidence_policy.cache_identity()
        or graph.get("source_contract_hash") != source_contract_hash(canonical)
        or graph.get("semantic_context_hash") != semantic_context_hash(metadata)
        or graph.get("classification_mode")
        != "api_classified_and_verified"
        or graph.get("status") not in {
            "ready", "review_required", "failed",
        }
    ):
        return None
    graph_pdf_sha256 = str(
        (graph.get("vision_evidence") or {}).get("pdf_sha256") or "")
    current_pdf_sha256 = str(
        (page_bundle or {}).get("pdf_sha256") or "")
    if graph_pdf_sha256 and current_pdf_sha256 != graph_pdf_sha256:
        return None
    semantic_source = render_semantic_source(graph, canonical)
    if graph.get("semantic_source_sha256") != _sha256_text(semantic_source):
        migrated = _migrate_machine_metadata_semantic_source(
            graph,
            canonical=canonical,
        )
        if migrated is None:
            return None
        graph, semantic_source = migrated
    if graph.get("status") != "ready":
        migrated_heading = (
            _migrate_adjudicated_heading_validation_false_positive(
                graph,
                canonical=canonical,
                semantic_source=semantic_source,
            )
        )
        if migrated_heading is not None:
            graph = migrated_heading
    if graph.get("status") == "ready":
        if graph.get(_SOURCE_REVIEW_KEY) is not None:
            return None
        if not _current_human_source_overrides_valid(
            graph,
            canonical=canonical,
            page_bundle=page_bundle,
        ):
            return None
        if validate_graph(
            graph,
            canonical=canonical,
            semantic_source=semantic_source,
        ):
            return None
        return graph
    errors = validate_graph(
        graph,
        canonical=canonical,
        semantic_source=semantic_source,
        allow_unresolved_source_anomalies=True,
    )
    allowed_codes = {
        "semantic_source_rich_text",
        "numbered_main_topic_coverage",
    }
    if any(
        str(row.get("code") or "") not in allowed_codes
        for row in errors
        if isinstance(row, dict)
    ):
        return None
    if not _human_reviewable_source_graph(
        graph,
        canonical=canonical,
        page_bundle=page_bundle,
    ):
        return None
    review = graph.get(_SOURCE_REVIEW_KEY)
    if review is not None:
        if (
            not isinstance(review, dict)
            or review.get("version") != _SOURCE_REVIEW_VERSION
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(review.get("context_hash") or ""),
            )
            or not str(review.get("decision_id") or "").startswith(
                "phase3-source-")
            or str(review.get("pdf_sha256") or "") != graph_pdf_sha256
            or str(review.get("pdf_sha256") or "") != current_pdf_sha256
        ):
            return None
        review_candidates = review.get("candidates") or []
        if review.get("review_kind") == "canonical_topic_patch":
            try:
                current_preview = _numbered_topic_patch_preview(
                    graph,
                    canonical=canonical,
                    page_bundle=page_bundle,
                )
            except (TypeError, ValueError):
                return None
            saved_patch = review.get("source_patch")
            if current_preview is None or not isinstance(saved_patch, dict):
                return None
            _repaired, current_patch = current_preview
            sealed_preview_fields = (
                "version", "kind", "target", "verified",
                "raw_source_mutated", "source_contract_hash",
                "semantic_context_hash", "before_sha256", "after_sha256",
                "patch_hash", "target_id", "before", "after", "operations",
            )
            if any(
                saved_patch.get(field) != current_patch.get(field)
                for field in sealed_preview_fields
            ):
                return None
            review_item = review.get("item")
            if not isinstance(review_item, dict) or (
                str(review_item.get("raw_text") or "")
                != str(current_patch.get("before") or "")
                or str(review_item.get("raw_sha256") or "")
                != _sha256_text(current_patch.get("before") or "")
            ):
                return None
            if len(review_candidates) != 1 or (
                str(review_candidates[0].get("target_id") or "")
                != str(current_patch.get("target_id") or "")
                or str(review_candidates[0].get("resolved_text") or "")
                != str(current_patch.get("after") or "")
                or str(review.get("recommended_target_id") or "")
                != str(current_patch.get("target_id") or "")
            ):
                return None
        for candidate in review_candidates:
            if not isinstance(candidate, dict):
                return None
            resolved = str(candidate.get("resolved_text") or "")
            if (
                not resolved
                or candidate.get("resolved_sha256")
                != _sha256_text(resolved)
            ):
                return None
        expected_context_hash = _source_review_context_hash(
            graph,
            item=dict(review.get("item") or {}),
            candidates=[
                dict(row) for row in review_candidates
                if isinstance(row, dict)
            ],
            pdf_sha256=str(review.get("pdf_sha256") or ""),
            revision=dict(review.get("revision") or {}),
        )
        if (
            review.get("context_hash") != expected_context_hash
            or review.get("decision_id")
            != f"phase3-source-{expected_context_hash[:24]}"
        ):
            return None
    return graph


def load_verified_generation_graph(
    directory: Path,
    canonical: dict[str, Any],
    metadata: dict[str, Any],
    *,
    page_bundle: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str] | None:
    """Return only a fully verified, current, internally consistent graph.

    A compatible concept checkpoint never waives source verification. The live
    path may avoid a repeated model call only when an earlier graph for the same
    complete ACSD/overlay contract was independently API-verified and its
    deterministic semantic rendering still validates byte-for-byte.
    """
    graph = load_graph(directory, canonical)
    if not isinstance(graph, dict):
        return None
    if graph.get("status") != "ready":
        return None
    if graph.get("classification_mode") != "api_classified_and_verified":
        return None
    if graph.get("semantic_context_hash") != semantic_context_hash(metadata):
        return None
    graph_pdf_sha256 = str(
        (graph.get("vision_evidence") or {}).get("pdf_sha256") or "")
    current_pdf_sha256 = str(
        (page_bundle or {}).get("pdf_sha256") or "")
    if graph_pdf_sha256 and current_pdf_sha256 != graph_pdf_sha256:
        return None
    semantic_path = Path(directory) / SEMANTIC_SOURCE_FILENAME
    try:
        semantic_source = semantic_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if graph.get("semantic_source_sha256") != _sha256_text(semantic_source):
        return None
    if semantic_source != render_semantic_source(graph, canonical):
        return None
    if not _current_human_source_overrides_valid(
        graph,
        canonical=canonical,
        page_bundle=page_bundle,
    ):
        return None
    if validate_graph(
        graph,
        canonical=canonical,
        semantic_source=semantic_source,
    ):
        return None
    return graph, semantic_source


def checkpoint_resume_graph_safe(
    graph: dict[str, Any],
    *,
    canonical: dict[str, Any],
) -> bool:
    """Prove that a legacy checkpoint is attached to a verified source graph.

    A checkpoint preserves already-completed concept work, inventory, and mined
    Types.  It does not grant permission to resume on a deterministic guess.  A
    cached graph is reusable only after API classification, independent semantic
    criticism, current-contract validation, and complete QID/topic ownership.
    """
    if graph.get("status") != "ready":
        return False
    if graph.get("classification_mode") != "api_classified_and_verified":
        return False
    if (
        graph.get("semantic_confidence_policy")
        != confidence_policy.cache_identity()
    ):
        return False
    if not graph.get("topics"):
        return False
    if any(
        row.get("severity") == "error"
        for row in graph.get("issues") or []
        if isinstance(row, dict)
    ):
        return False
    semantic_source = render_semantic_source(graph, canonical)
    if validate_graph(
        graph,
        canonical=canonical,
        semantic_source=semantic_source,
    ):
        return False
    topic_ids = {
        str(row.get("topic_id") or "")
        for row in graph.get("topics") or []
        if isinstance(row, dict)
    }
    if not topic_ids:
        return False
    for task in graph.get("tasks") or []:
        if not isinstance(task, dict):
            return False
        if str(task.get("topic_id") or "") not in topic_ids:
            return False
        if not str(task.get("qid") or ""):
            return False
    return True


def prepare_generation_graph(
    *,
    canonical: dict[str, Any],
    source_text: str,
    metadata: dict[str, Any],
    source_path: Path | None,
    artifact_dir: Path,
    verify_semantics: bool = True,
    resume_review_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    page_bundle = None
    if source_path is not None and source_path.exists():
        page_bundle = load_page_evidence(
            source_path,
            artifact_dir,
            job_id=(active_session() or {}).get("job_id"),
        )
    if verify_semantics:
        artifact_graph = load_graph(artifact_dir, canonical)
        cached = load_verified_generation_graph(
            artifact_dir,
            canonical,
            metadata,
            page_bundle=page_bundle,
        )
        if cached is not None:
            graph, _semantic_source = cached
            progress.log(
                "Reused the API-verified Phase 3 semantic graph for the current "
                "source contract; no hierarchy model call was repeated.",
                level="success",
            )
            return graph

        validated_resume = None
        if resume_review_graph is not None:
            validated_resume = _compatible_source_review_graph(
                resume_review_graph,
                canonical=canonical,
                metadata=metadata,
                page_bundle=page_bundle,
            )
            if validated_resume is None:
                raise ValueError(
                    "The saved source-review checkpoint no longer matches the "
                    "current original-PDF evidence or semantic source. Restore "
                    "the original file, or replace it and convert again; no "
                    "model retry was started."
                )
        validated_artifact = _compatible_source_review_graph(
            artifact_graph,
            canonical=canonical,
            metadata=metadata,
            page_bundle=page_bundle,
        )
        artifact_pdf_sha256 = str(
            ((artifact_graph or {}).get("vision_evidence") or {}).get(
                "pdf_sha256"
            )
            or ""
        )
        current_pdf_sha256 = str(
            (page_bundle or {}).get("pdf_sha256") or "")
        if (
            artifact_pdf_sha256
            and current_pdf_sha256 != artifact_pdf_sha256
        ):
            raise ValueError(
                "The cached semantic graph no longer matches the current "
                "original-PDF evidence. Restore the original file, or replace "
                "it and convert again; no model retry was started."
            )
        if (
            _has_human_selected_source_overrides(artifact_graph)
            and validated_artifact is None
        ):
            raise ValueError(
                "The human-corrected source graph no longer matches the "
                "current original-PDF evidence. Restore the original file, "
                "or replace it and convert again; no model retry was started."
            )
        unresolved_candidates = [validated_resume, validated_artifact]
        for candidate in unresolved_candidates:
            if not isinstance(candidate, dict):
                continue
            graph = candidate
            session = active_session()
            if isinstance(session, dict):
                session["graph"] = graph
            try:
                graph = _source_review_graph_or_raise(
                    graph,
                    canonical=canonical,
                    page_bundle=page_bundle,
                    source_path=source_path,
                )
            except semantic_recovery.HumanDecisionRequired:
                semantic_source = render_semantic_source(graph, canonical)
                graph["semantic_source_sha256"] = _sha256_text(
                    semantic_source)
                write_artifacts(
                    artifact_dir,
                    graph=graph,
                    report=_updated_graph_report(graph),
                    semantic_source=semantic_source,
                    page_bundle=page_bundle,
                )
                if isinstance(session, dict):
                    session["graph"] = graph
                raise
            semantic_source = render_semantic_source(graph, canonical)
            graph["semantic_source_sha256"] = _sha256_text(semantic_source)
            write_artifacts(
                artifact_dir,
                graph=graph,
                report=_updated_graph_report(graph),
                semantic_source=semantic_source,
                page_bundle=page_bundle,
            )
            if graph.get("status") == "ready":
                if graph.get(_ADJUDICATED_HEADING_MIGRATION_KEY):
                    progress.log(
                        "Revalidated the existing API-verified Phase 3 graph "
                        "against the sealed Phase 2.2 PDF heading; no hierarchy "
                        "model call was repeated.",
                        level="success",
                    )
                else:
                    progress.log(
                        "Applied the saved human source decision from verified "
                        "PDF evidence; semantic generation is continuing without "
                        "repeating hierarchy calls.",
                        level="success",
                    )
                if isinstance(session, dict):
                    session["graph"] = graph
                return graph
    hierarchy_provider = (
        _classify_hierarchy_via_openai
        if verify_semantics and semantic_api_enabled() else None
    )
    critic_provider = (
        _critic_hierarchy_via_openai
        if verify_semantics and semantic_api_enabled() else None
    )
    graph, report = compile_semantic_graph(
        canonical,
        source_text=source_text,
        metadata=metadata,
        page_bundle=page_bundle,
        hierarchy_provider=hierarchy_provider,
        critic_provider=critic_provider,
    )
    def _automatic_reconciliation_allowed() -> bool:
        # Under the rewritten pipeline the converter-markup anomaly is
        # adjudicated automatically: the provider decides against the
        # verified original-PDF page evidence (the same material a human
        # reviewer would be shown) and its verdict ships in the audit.
        # The legacy path keeps the explicit human decision.
        try:
            from .phase3 import runner as _phase3_runner
        except ImportError:  # pragma: no cover - defensive ordering
            return False
        return _phase3_runner.rewrite_enabled()

    graph = reconcile_source_anomalies(
        graph,
        canonical=canonical,
        page_bundle=page_bundle,
        source_path=source_path,
        allow_automatic_reconciliation=_automatic_reconciliation_allowed(),
    )
    semantic_source = render_semantic_source(graph, canonical)
    graph["semantic_source_sha256"] = _sha256_text(semantic_source)
    report["status"] = graph.get("status")
    report["semantic_source_sha256"] = graph.get("semantic_source_sha256")
    report["issues"] = copy.deepcopy(graph.get("issues") or [])
    report.setdefault("summary", {})["errors"] = sum(
        1 for row in graph.get("issues") or [] if row.get("severity") == "error"
    )
    report.setdefault("summary", {})["warnings"] = sum(
        1 for row in graph.get("issues") or [] if row.get("severity") == "warning"
    )
    report.setdefault("summary", {})["source_fusion_repairs"] = len(
        graph.get("source_fusion_repairs") or []
    )
    if graph.get("status") != "ready":
        session = active_session()
        if isinstance(session, dict):
            session["graph"] = graph
        if _human_reviewable_source_graph(
            graph,
            canonical=canonical,
            page_bundle=page_bundle,
        ):
            try:
                graph = _source_review_graph_or_raise(
                    graph,
                    canonical=canonical,
                    page_bundle=page_bundle,
                    source_path=source_path,
                )
            except semantic_recovery.HumanDecisionRequired:
                semantic_source = render_semantic_source(graph, canonical)
                graph["semantic_source_sha256"] = _sha256_text(
                    semantic_source)
                write_artifacts(
                    artifact_dir,
                    graph=graph,
                    report=_updated_graph_report(graph, report),
                    semantic_source=semantic_source,
                    page_bundle=page_bundle,
                )
                if isinstance(session, dict):
                    session["graph"] = graph
                raise
            semantic_source = render_semantic_source(graph, canonical)
            graph["semantic_source_sha256"] = _sha256_text(semantic_source)
            report = _updated_graph_report(graph, report)
    write_artifacts(
        artifact_dir,
        graph=graph,
        report=_updated_graph_report(graph, report),
        semantic_source=semantic_source,
        page_bundle=page_bundle,
    )
    if graph.get("status") != "ready":
        # This should only remain reachable for integrity failures that cannot
        # be represented as a bounded human source decision.
        details = "; ".join(
            str(row.get("message") or row.get("code") or "")
            for row in graph.get("issues") or []
            if row.get("severity") == "error"
        )
        raise ValueError(
            "Phase 3 semantic source graph is not safe for concept generation: "
            + (details or "source review required")
        )
    progress.log(
        "Phase 3 semantic graph active: "
        f"{len(graph.get('topics') or [])} stable topic(s), "
        f"{len(graph.get('subtopics') or [])} subtopic(s), "
        f"{len(graph.get('tasks') or [])} QID task(s); semantic generation now "
        "uses graph-controlled source packets rather than flat raw MMD.",
        level="success",
    )
    return graph


@contextmanager
def activate(graph: dict[str, Any]) -> Iterator[None]:
    token = _ACTIVE_GRAPH.set(graph)
    try:
        yield
    finally:
        _ACTIVE_GRAPH.reset(token)


def active_graph() -> dict[str, Any] | None:
    value = _ACTIVE_GRAPH.get()
    return value if isinstance(value, dict) else None


@contextmanager
def activate_session(session: dict[str, Any]) -> Iterator[None]:
    token = _ACTIVE_SESSION.set(session)
    try:
        yield
    finally:
        _ACTIVE_SESSION.reset(token)


def active_session() -> dict[str, Any] | None:
    value = _ACTIVE_SESSION.get()
    return value if isinstance(value, dict) else None


def semantic_source_for_active_graph(raw_source: str) -> str:
    graph = active_graph()
    session = active_session()
    if not isinstance(graph, dict) or not isinstance(session, dict):
        return str(raw_source or "")
    canonical = session.get("canonical")
    if not isinstance(canonical, dict):
        return str(raw_source or "")
    return render_semantic_source(graph, canonical)


def artifact_manifest(directory: Path) -> dict[str, Any]:
    directory = Path(directory)
    graph_path = directory / GRAPH_FILENAME
    report_path = directory / GRAPH_REPORT_FILENAME
    report: dict[str, Any] = {}
    if report_path.exists():
        try:
            value = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                report = value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            report = {}
    files: list[dict[str, Any]] = []
    for kind, spec in ARTIFACT_SPECS.items():
        path = directory / spec["filename"]
        if not path.exists():
            continue
        files.append({
            "kind": kind,
            "label": spec["label"],
            "filename": spec["filename"],
            "media_type": spec["media_type"],
            "size_bytes": path.stat().st_size,
        })
    return {
        "available": graph_path.exists(),
        "status": str(report.get("status") or "unavailable"),
        "schema_version": str(report.get("schema_version") or SCHEMA_VERSION),
        "compiler_version": str(report.get("compiler_version") or COMPILER_VERSION),
        "phase": PHASE,
        "source_contract_hash": str(report.get("source_contract_hash") or ""),
        "semantic_context_hash": str(report.get("semantic_context_hash") or ""),
        "semantic_source_sha256": str(report.get("semantic_source_sha256") or ""),
        "classification_mode": str(report.get("classification_mode") or ""),
        "summary": copy.deepcopy(report.get("summary") or {}),
        "files": files,
    }


def artifact_path(directory: Path, kind: str) -> tuple[Path, dict[str, str]]:
    spec = ARTIFACT_SPECS.get(str(kind or ""))
    if spec is None:
        raise ValueError("unknown Phase 3 source artifact")
    path = Path(directory) / spec["filename"]
    if not path.exists():
        raise ValueError("Phase 3 source artifact is unavailable")
    return path, spec
