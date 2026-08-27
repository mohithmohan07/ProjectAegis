"""Pass 2.9 — Premap: the Pre-Learning concept map (doc §4 Phase 03).

docs/aegis-restructure.md §4 Phase 03: *"At the end, that capture is
built into a complete Pre-Learning concept map with the same detailing
standard as Post-Learning."* Slice B (``prelearn.py``) made the running
capture and consolidated it; this pass builds the map **from that
capture**.

**Built from the capture, never from the Post map.** The evidence is the
merged prerequisite set and the source blocks those prerequisites were
read from — never the finished Post concepts. Deriving a Pre map from
Post conclusions is the "derive from existing chapters" flow Q3 retired
and this branch already deleted. The Post lane appears exactly once in
this pass, and only at the end: the needed-for links are decided by
reading the Post concepts' *Descriptions* to LINK to them, which is a
different act from building the Pre map out of them. The pin for that
distinction is in the regressions.

**NO EXTRACTION OF ANY QUESTIONS, ANYWHERE (owner steer, 17 Aug 2026,
docs/restructure-handoff.md).** No question is ever lifted out of the
source into any Pre artefact. Concretely:

* a Pre row ships **Description and Achieving Mastery only**, plus the
  ``Misconception/ Error Analysis`` section on the rows the Pre lane's
  own Q1 inventory allotted an item to (``preanalyse.py``, stamped
  below) — the same shape Settle + Analyse produce for a Post row
  (``settle.py``: *"Q1: Settle mints Description + Achieving Mastery
  only"*). Types, Cases and Examples reach the Pre detailing only once
  GENERATED questions exist to fill them, which is a later slice's work.
  There is no code path here that can mint one, and the map decision
  itself may author neither those nor an analysis section — the only
  writer of the analysis section is the inventory's allotment;
* Post's Examples ARE source questions (Rule C: every QID has exactly
  one final Type/Case assignment), so lifting one onto a Pre concept
  would ALSO double-route that QID and break Q2's exactly-once — the
  steer and the existing uniqueness law agree;
* the guard is mechanical and **fails closed** on what this lane
  AUTHORS: ``_refuse_source_qids`` refuses any finished Pre row, and any
  pre-topic the map authored, that carries an id from the chapter's
  question/task inventory. That is identity accounting over minted ids
  — parsing and marker bookkeeping, which CLAUDE.md allows — not a
  reading of what the content means. It is deliberately NOT advisory and
  deliberately NOT routed to The Fixer: a source QID on a Pre row is a
  double-routing of that QID, a decision that cannot be made
  mechanically applicable, which is one of CLAUDE.md's own stopping
  conditions;
* the same accounting over what this lane is SHOWN is a **redaction**,
  not a refusal (``_redact_ids``). Upstream evidence — the capture's own
  rationale, a Post Description — may legitimately name the question it
  was read from, and refusing that would permanently brick a run holding
  a finished Post map. Dropping an id token from provenance is plainly
  mechanically applicable, so CLAUDE.md's stopping carve-out does not
  cover it. Which of the two acts applies is decided by who wrote the
  text, never by what the text says;
* review flags are outside the guarded surface, by ordering: the guard
  runs before any flag is stamped. A flag is auditor prose ABOUT the
  artefact; treating it as content would let the advisory critic's own
  dissent gate the run, which Q10 forbids in as many words.

**What the guard does NOT cover, stated plainly.** It is QID *identity*
accounting. A source question's WORDING, copied through with its id
removed, is not mechanically refused anywhere in this lane — by design
(spec T4c): content leakage is one of the critic's four advisory
dimensions, because a leakage test keyed on string-matching the chapter
text is exactly the shape-matching Rule 1 forbids. So "no question
wording copied through" rests on the model plus an advisory flag, and
"the guard is mechanical and fails closed" must not be read as covering
it. The channel is live, not theoretical: ``prelearn``'s host-stage
capture shows the model each question's text.

**NO QUOTA.** How many pre-topics and pre-concepts the captured evidence
supports is entirely the model's judgment. Nothing here scales a count
from evidence volume — not from the number of prerequisites, not from
the number of Post concepts, not from source size (Rule 1: no
volume-derived structure; §9 records that a "4-6 topics / 5-7 concepts"
quota engine was retired from this very lane). A thin capture yields a
small map and is never padded; an EMPTY capture yields an empty map
carrying one recorded verdict on whether that emptiness is correct.

**An empty capture is a judgment, not a shape** (spec-step8 D8.3 / S9).
This pass used to return the empty map "without spending a decision",
which answered "does this chapter genuinely assume no prior knowledge?"
by looking at the length of a list — and an OCR-degraded scan, a thin
lower-grade chapter and a chapter that genuinely opens from nothing are
indistinguishable under that inference. So the question goes to the
model against the chapter's own source, as ONE decision on its own kind
(``premap.empty_capture``) with its own policy version, an advisory
critic (Q10) and a Fixer seam (Q13). The verdict rides
``pre_lane_verdict`` on the returned map; ``build_concepts_release.
structural_defects`` reads it and never re-decides it.

**Calibration is evidence, never a branch** (steer point 2). The
chapter's own board, grade, subject and unit ride the payload as
evidence, and The Architect's assembled instruction set (§8.1) rides it
through ``prompts.instruction_rules_suffix`` — the same mechanism
Settle, Host, Analyse and the capture already use, widened here to
include the board/publication slot the Pre lane needs. No line of this
module branches on a grade, a subject, or a board value; a board-name
substring test deciding how a prerequisite should read would be Rule 1's
forbidden judgment wearing a curriculum hat (the retired pre-learning
generator carried exactly such a switch).

**Grounding (spec T4).** A Pre concept has ZERO current-chapter
grounding by definition — that is what makes it a prerequisite. It
cannot be grounded on the chapter's introduction or review blocks
either: the doc's Sorrieu passage rules that out explicitly (a chapter
opening with a scene from later in the plot is not pre-learning, and
treating it as such would lose it). So a Pre row carries the distinct
contract ``derived-from-prerequisite-capture``, following settle.py's
existing precedent for a non-source contract value
(``derived-from-verified-topic-concepts``), and the seal excludes those
rows from the current-chapter block check — narrowly, and in both
directions: a row on this contract must carry NO chapter block id at
all, and every other row is sealed exactly as before.

Four things are recorded here as decisions rather than left as
omissions:

* ``PRE_VALIDATOR_FLAGS`` — the Pre lane's ``concept_validator`` stance,
  each flag decided explicitly (see the constant's comments), including
  ``require_culmination=False`` and ``strict_type_hierarchy=False``.
* **The validator's findings are advisory here.** They are stamped onto
  their row as review flags; nothing is dropped and nothing raises.
  Five of the reachable codes are inherited Rule 1 residues in the
  SHARED validator, recorded here rather than authored around:
  ``description_length`` is a word-count band and ``thin_description`` a
  word-count floor (both decide meaning by counting words);
  ``placeholder`` is a keyword vocabulary — ``PLACEHOLDERS`` contains
  the word "none", tested as a whole-word scan over the whole detailing,
  so ordinary English ("faces none at all") is classified as placeholder
  text; it fires on 2 of the golden chapter's 15 Pre rows. Since the Pre
  lane grew its own Q1 inventory (``preanalyse.py``) two more are
  reachable, both VERB vocabularies over the rendered analysis section:
  ``misconception_framing`` (``_MISCONCEPTION_BELIEF_RE``'s verb list
  does not contain "take … to mean") and ``error_analysis_framing``
  (``_ERROR_ANALYSIS_ACTION_RE``'s does not contain "carry a claim …
  across"); each fires on 1 of the golden chapter's 15 Pre rows over a
  well-framed item. All five can fire on a perfectly well-authored
  prerequisite row, which is why promoting Pre warnings to fatals
  wholesale would fail exactly the rows the format asks for. They are
  NOT scoped away here — hiding a Rule 1 defect is not purging it (spec
  T4's §3 purge doctrine) — and no authored item is ever reworded to
  satisfy one, which would hide it just as effectively. They are not
  fixed here either: all five are pre-existing shared Post-lane
  machinery whose real purge is replacing each with a model verdict,
  which moves the Post lane and belongs to its own change. The deposit
  gate is a later slice's, and it inherits these recorded flags.
* **The four verifications are ADVISORY (Q10, spec T4c).** Necessity,
  grade boundary, non-duplication and zero current-chapter content
  leakage are critic dimensions whose dissent becomes a review flag —
  never a gate, a retry, or an escalation. A grade-boundary test keyed
  on a year number would be a numeric threshold deciding meaning; a
  leakage test keyed on string-matching the chapter text would be
  shape-matching. The one mechanical member of that family is the
  source-QID guard above, which is identity accounting and DOES fail
  closed.
* **Exact-once over the captured set (R4).** Every merged prerequisite
  is mapped by exactly one pre-concept: a prerequisite named by nothing
  is a learner's prerequisite lost silently, and one named twice is a
  double-count. How prerequisites group into concepts, and how concepts
  group into topics, stays entirely the model's judgment.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from . import envelope as envelope_mod
from . import kernel
from ... import config
from .. import katex_rules as kr
from .. import progress

POLICY_VERSION = "premap-1"

# --------------------------------------------------------------------------- #
# The empty-capture verdict (spec-step8 D8.3 / S9)
#
# A NEW decision kind with its OWN policy version, and never an edit to
# ``premap.map`` / ``premap.needed_for`` or to ``POLICY_VERSION`` above:
# a decision key is (kind, unit, envelope, payload, policy_version), so
# changing an existing one replays verdicts made under different prompt
# text against the new text — cache poisoning with no symptom (spec §4,
# "the phase3 modules' decision identities").
#
# WHY IT EXISTS. This pass used to return the empty map "without spending
# a decision", inferring from an empty list that the chapter needs
# nothing. An OCR-degraded scan, a thin lower-grade chapter and a chapter
# that genuinely assumes nothing are indistinguishable under that
# inference — and it is exactly the shape-matching Rule 1 forbids ("the
# list is empty, therefore the chapter needs nothing"). So the question
# goes to the model with the chapter's own source in hand, with an
# advisory critic (Q10) and a Fixer seam (Q13). It costs one call, and it
# is what let §4 keep its three release-state names.
# --------------------------------------------------------------------------- #

EMPTY_CAPTURE_KIND = "premap.empty_capture"
EMPTY_CAPTURE_POLICY_VERSION = "premap-empty-capture-1"
EMPTY_CAPTURE_VERDICT_FIELD = "pre_lane_verdict"
ASSUMES_NOTHING = "assumes_nothing"
CAPTURE_INCOMPLETE = "capture_incomplete"
EMPTY_CAPTURE_VERDICTS = (ASSUMES_NOTHING, CAPTURE_INCOMPLETE)

# The Pre lane's grounding contract (spec T4). A Pre row is grounded on
# the run's own prerequisite capture, not on chapter source blocks.
PRE_GROUNDING_CONTRACT = "derived-from-prerequisite-capture"

# Row-private audit fields (docs/restructure-handoff.md's marker pattern,
# steps 4-6). Registered in build_concepts_release._RELEASE_AUDIT_FIELDS
# and stripped before DB upload; their column home is step 8's
# ``related_concepts`` (Q5). Neither adds a house-format section — the
# detailing parsers are shared with the Post lane, so a new canonical
# section would change what every POST row may contain.
NEEDED_FOR_FIELD = "_aegis_needed_for"
PREREQUISITES_FIELD = "_aegis_pre_prerequisites"

# Pre concepts are linked in bounded batches (payload-size mechanics
# only — analyse.py's precedent; the map itself carries no count
# structure of any kind).
_LINK_BATCH_SIZE = 8

# The Pre lane's concept_validator stance. Every flag is decided here,
# none by default (spec T4):
#
# * ``require_culmination=False`` — DECIDED OFF for Pre, not omitted. A
#   culmination consolidates a topic's concepts into what the learner can
#   now do with them combined; a prerequisite topic exists to be already
#   known before the chapter starts, so there is nothing for it to
#   consolidate. Leaving it on would emit ``culmination_count`` against
#   every multi-concept Pre topic and pressure a recap row that teaches
#   nothing.
# * ``allow_types=False`` — the binding constraint, expressed as marker
#   accounting: a Pre row has no Types at all in this slice, so a Types
#   section on one is a defect worth naming (``types_too_early``).
# * ``strict_type_hierarchy=False`` — DECIDED OFF, and moot by
#   construction. It activates six codes (missing/generic/duplicate type
#   definition, missing/generic case definition, case_without_example)
#   against Type/Case/Example structures. Under the no-extraction steer
#   those must not exist on a Pre row at all, so turning the flag on
#   would gate Pre rows on the absence of something the lane is forbidden
#   to have. ``allow_types=False`` is the check that actually belongs
#   here.
# * ``strict_mastery_statement=False`` — DECIDED OFF because of what it
#   activates: ``mastery_statement_not_substantive`` is decided by
#   ``_is_substantive_mastery_statement``, which is a word-count and
#   character-count threshold (four words, twelve characters) deciding
#   whether a sentence means enough. The Pre lane will not adopt a
#   word-count judgment (Rule 1). The canonical
#   ``\nAchieving Mastery: <text>`` shape that flag also checks is
#   guaranteed here by construction — this module mints the line — which
#   is a stronger guarantee than a validator flag.
# * ``strict_analysis_section=False`` and ``analysis_allotted_keys`` —
#   Q1: a concept's Misconception/ Error Analysis section exists only
#   where an inventory item was allotted to it. The Pre lane now has its
#   own inventory (``preanalyse.py``), so the key set is DERIVED from the
#   rows' own allotment markers at validation time
#   (``validate_rows``) rather than pinned empty here: it scopes the
#   existence codes to the allotted rows and keeps
#   ``unallotted_analysis_section`` live as marker accounting over the
#   rest. ``strict_analysis_section`` stays DECIDED OFF: it activates the
#   shared validator's Post-lane analysis QUALITY codes, and the Pre
#   lane's validation is advisory in this slice, so turning them on would
#   add Post-flavoured prose judgments to a prerequisite row without
#   gating anything. The canonical section SHAPE is guaranteed here by
#   construction — ``preanalyse.stamp`` mints it — which is the stronger
#   guarantee.
# * ``source_text=""`` — the verbatim-source check compares against the
#   CURRENT chapter's prose. A prerequisite description is about material
#   from before this chapter, so that comparison has no meaning here.
PRE_VALIDATOR_FLAGS: dict[str, Any] = {
    "require_parent": False,
    "allow_types": False,
    "require_culmination": False,
    "allow_culmination": True,
    "allowed_source_examples": (),
    "strict_type_hierarchy": False,
    "strict_analysis_section": False,
    "strict_mastery_statement": False,
    "source_text": "",
    # Derived per call from the rows' own allotment markers; the constant
    # keeps the default the Pre lane means when nothing was allotted.
    "analysis_allotted_keys": frozenset(),
}

# Mechanical id parsing (CLAUDE.md: parsing and ID assignment may be
# deterministic). ``build_concepts_release._QID_RE`` is the same idea at
# the release boundary.
_ID_TOKEN_BOUNDARY = re.compile(r"[0-9A-Za-z]")

# What a redacted source-question identity reads as in evidence text.
# Not an id, so it can never be mistaken for one, and it leaves the
# sentence readable.
_REDACTED_QID = "[a source question of this chapter]"


class PreExtractionError(RuntimeError):
    """A Pre artefact carries a source question's identity.

    Fail-closed, by the owner's steer. Never advisory, never routed to
    The Fixer: the QID would then have two final homes and Q2's
    exactly-once (Rule C) has no correction that keeps both.
    """


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def mint_pre_topic_ids(count: int) -> list[str]:
    """Deterministic positional pre-topic ids: PRT-0001, PRT-0002, …"""
    return [f"PRT-{index:04d}" for index in range(1, count + 1)]


def mint_pre_concept_ids(count: int) -> list[str]:
    """Deterministic positional pre-concept ids: PRC-0001, PRC-0002, …"""
    return [f"PRC-{index:04d}" for index in range(1, count + 1)]


def _description_of(details: object) -> str:
    match = re.search(
        r"Description:\s*(.*?)(?=\n[A-Z][A-Za-z ]{2,24}:|//|$)",
        str(details or ""),
        re.DOTALL,
    )
    return _normal(match.group(1)) if match else ""


def _ref_list(value: object) -> list[str]:
    if isinstance(value, (str, bytes)):
        rows: list[Any] = [value]
    elif isinstance(value, (list, tuple)):
        rows = list(value)
    elif value in (None, ""):
        rows = []
    else:
        rows = [value]
    return [text for text in (_normal(row) for row in rows) if text]


# ---------------------------------------------------------------------------
# the mechanical no-extraction guard


# Every field of an inventory item that carries a question's own
# identity. ``qid`` is the item's id; ``parent_qid`` /
# ``_acsd_parent_qid`` name the UMBRELLA question a sub-part hangs
# under, and an umbrella is a real current-chapter question whose stem
# is the item's ``shared_context`` ("Write a note on:") — it is simply
# not itself an inventory row. Reading only ``qid`` would leave those
# umbrella identities outside the known set, so a Pre row could carry
# one unrefused.
_QID_FIELDS = ("qid", "parent_qid", "_acsd_parent_qid")


def inventory_qids(env: Mapping[str, Any]) -> list[str]:
    """Every QID in the chapter's question/task inventory.

    A known set of ids this pipeline minted — deliberately NOT a regex
    over anything shaped like a QID. ``build_concepts_release._QID_RE``
    catches the same family at the release boundary by pattern; the
    known set is the stronger half of that comparison, because it can
    never classify a string the pipeline did not mint.
    """

    qids: list[str] = []
    for item in (env.get("inventory") or {}).get("items") or []:
        if not isinstance(item, Mapping):
            continue
        for field in _QID_FIELDS:
            qid = str(item.get(field) or "").strip()
            if qid and qid not in qids:
                qids.append(qid)
    return qids


def _mentions_id(text: str, token: str) -> bool:
    """Whether ``token`` occurs in ``text`` as a whole id.

    Boundary-aware so ``QINV-0004`` does not match inside
    ``QINV-00041``. Identity accounting over minted ids only — it reads
    no meaning out of the text it scans.
    """

    start = 0
    while True:
        index = text.find(token, start)
        if index < 0:
            return False
        before = text[index - 1] if index else " "
        after = text[index + len(token):index + len(token) + 1] or " "
        if not (
            _ID_TOKEN_BOUNDARY.match(before)
            or _ID_TOKEN_BOUNDARY.match(after)
        ):
            return True
        start = index + 1


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        found: list[str] = []
        for key, entry in value.items():
            found.append(str(key))
            found.extend(_strings(entry))
        return found
    if isinstance(value, (list, tuple, set)):
        found = []
        for entry in value:
            found.extend(_strings(entry))
        return found
    return []


def _redact_ids(text: str, qids: list[str]) -> str:
    """Replace whole source-QID tokens in evidence text with a marker.

    The two halves of the no-extraction guard are deliberately different
    acts, and confusing them is what makes a run unrecoverable:

    * text the Pre lane is **shown** — the upstream capture's own
      ``text``/``rationale``, and the Post concepts' Descriptions — is
      REDACTED. A capture rationale that says "the exercise QINV-0004
      assumes sovereignty" is legitimate upstream output: ``prelearn``
      shows the host-stage model each question's id and text and asks it
      to say what in that evidence assumes the element, so provenance
      naming a QID is expected, not corruption. Refusing it would kill a
      run at 97% with a finished Post map in hand, permanently — the
      offending rationale lives in a content-addressed decision, so
      every replay reproduces the refusal at zero spend. CLAUDE.md
      allows a stop only where a decision "cannot be made mechanically
      applicable"; dropping an id token from provenance plainly can be,
      exactly as ``map_evidence`` already drops non-block citations from
      the evidence list. Nothing a learner would see is lost: the
      prerequisite's meaning survives, only the id leaves;
    * text the Pre lane **authors** — a finished Pre row, a pre-topic
      title — is REFUSED (``_refuse_source_qids``). That is the steer's
      own sentence, and it fails closed.

    Redaction keeps the property the pre-spend guard exists for: the
    model authoring the Pre map is never shown a source question's
    identity, so it cannot echo one.
    """

    for qid in qids:
        start = 0
        while True:
            index = text.find(qid, start)
            if index < 0:
                break
            before = text[index - 1] if index else " "
            after = text[index + len(qid):index + len(qid) + 1] or " "
            if (
                _ID_TOKEN_BOUNDARY.match(before)
                or _ID_TOKEN_BOUNDARY.match(after)
            ):
                start = index + 1
                continue
            text = text[:index] + _REDACTED_QID + text[index + len(qid):]
            start = index + len(_REDACTED_QID)
    return text


def _refuse_source_qids(value: Any, qids: list[str], *, where: str) -> None:
    """Fail closed when a source question's identity reaches the Pre lane.

    Applied to the evidence the Pre lane authors from AND to every
    finished Pre row, because the steer covers both: no question is ever
    lifted out of the source into any Pre artefact.
    """

    if not qids:
        return
    haystack = _strings(value)
    found = sorted({
        qid
        for qid in qids
        for text in haystack
        if _mentions_id(text, qid)
    })
    if found:
        raise PreExtractionError(
            f"{where} carries source question id(s) " + ", ".join(found)
            + "; no question is ever lifted out of the source into a "
            "Pre-Learning artefact (owner steer, 17 Aug 2026), and a "
            "source QID with two final homes breaks Q2's exactly-once"
        )


# ---------------------------------------------------------------------------
# evidence


def map_evidence(
    env: Mapping[str, Any],
    prerequisites: Mapping[str, Any],
    qids: list[str] | None = None,
) -> dict[str, Any]:
    """The captured prerequisite set, and the source it was read from.

    The prerequisite rows come straight from ``prelearn.merge``. Their
    cited evidence is carried as provenance, **filtered to source block
    ids** — the capture also cites QIDs, LA-item ids and pooled item
    refs, and a QID must never ride a payload the Pre lane authors from.
    The capture's free text is filtered the same way but by
    ``_redact_ids`` rather than by refusal: an upstream rationale naming
    the question it read the prerequisite out of is expected output, and
    refusing it would brick a finished run permanently (see
    ``_redact_ids``). Block texts are resolved from the canonical source
    so a prerequisite stays readable against the prose that assumes it.
    No counts, ratios or size-derived fields ride any of it.
    """

    qids = list(qids or [])

    text_by_id = {
        str(row.get("block_id") or ""): str(row.get("display_text") or "")
        for row in env["canonical"]["blocks"]
        if isinstance(row, Mapping)
    }
    kind_by_id = {
        str(row.get("block_id") or ""): str(row.get("kind") or "")
        for row in env["graph"]["blocks"]
        if isinstance(row, Mapping)
    }
    rows: list[dict[str, Any]] = []
    cited_blocks: list[str] = []
    for item in prerequisites.get("prerequisites") or []:
        if not isinstance(item, Mapping):
            continue
        blocks = [
            ref for ref in _ref_list(item.get("evidence"))
            if ref in kind_by_id
        ]
        for ref in blocks:
            if ref not in cited_blocks:
                cited_blocks.append(ref)
        rows.append({
            "prerequisite_id": str(item.get("prerequisite_id") or ""),
            "text": _redact_ids(_normal(item.get("text")), qids),
            "rationale": _redact_ids(_normal(item.get("rationale")), qids),
            "source_block_ids": blocks,
        })
    return {
        "prerequisites": rows,
        "source_blocks": [
            {
                "block_id": ref,
                "kind": kind_by_id.get(ref, ""),
                "text": _redact_ids(text_by_id.get(ref, ""), qids),
            }
            for ref in cited_blocks
        ],
    }


def empty_capture_evidence(
    env: Mapping[str, Any], qids: list[str] | None = None,
) -> dict[str, Any]:
    """The chapter's own source, for the empty-capture verdict.

    The capture returned nothing, so there is no captured row to reason
    from — the only evidence that can answer "does this chapter genuinely
    assume no prior knowledge, or did the capture fail to reach it?" is the
    chapter itself. This is the same ``source_blocks`` shape
    ``prelearn.stage_evidence`` builds for the capture that came back
    empty, so the verdict is taken over the material the capture saw.

    QIDs are redacted exactly as ``map_evidence`` redacts them: a source
    question's identity may not ride a payload the Pre lane authors.
    No counts, ratios or size-derived fields ride any of it.
    """

    qids = list(qids or [])
    text_by_id = {
        str(row.get("block_id") or ""): str(row.get("display_text") or "")
        for row in env["canonical"]["blocks"]
        if isinstance(row, Mapping)
    }
    return {
        "source_blocks": [
            {
                "block_id": str(row.get("block_id") or ""),
                "topic_id": str(row.get("topic_id") or ""),
                "kind": str(row.get("kind") or ""),
                "text": _redact_ids(
                    text_by_id.get(str(row.get("block_id") or ""), ""), qids
                ),
            }
            for row in env["graph"]["blocks"]
            if isinstance(row, Mapping) and str(row.get("block_id") or "")
        ],
    }


def chapter_calibration(env: Mapping[str, Any]) -> dict[str, str]:
    """The chapter's own board/grade/subject/unit, as evidence.

    Steer point 2: a prerequisite authored for a Grade 6 science chapter
    and one for a Grade 10 mathematics chapter are different artefacts
    even where the underlying fundamental is the same. These values are
    read by the MODEL; no code in this module branches on any of them.
    """

    metadata = env.get("metadata") if isinstance(env, Mapping) else None
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return {
        key: _normal(metadata.get(key))
        for key in ("board", "grade", "subject", "unit", "chapter_title")
    }


# ---------------------------------------------------------------------------
# mechanics-only checkers


def compose_details(description: object, achieving_mastery: object) -> str:
    """The house format, exactly as settle.py mints it for a Post row."""

    return (
        "Description: " + _normal(description)
        + "\nAchieving Mastery: " + _normal(achieving_mastery)
    )


def _extra_sections(details: str) -> list[str]:
    """House-format section labels beyond the one a Pre row may carry.

    Marker accounting over the separator this pipeline itself mints
    (``concept_refiner.split_sections`` splits on ``" // "``, the same
    read ``assemble._inject_types`` writes and ``concept_validator``
    parses) — it makes no judgment about what the text means. A Pre row
    ships Description and Achieving Mastery and nothing else (owner
    steer), so any further section is a mechanical defect.
    """

    from .. import concept_refiner as cr

    return [
        label
        for label, _ in cr.split_sections(details)[1:]
        if label
    ]


def _map_checker(
    prerequisite_ids: list[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    """Mechanics only: positional ids, non-empty fields, R4 exact-once.

    How many pre-topics and pre-concepts the capture supports, and which
    prerequisites belong together, is the model's authored judgment (the
    advisory critic reviews it). Nothing here bounds a count.

    The one structural refusal is the no-extraction steer's other half:
    a Pre row carries no Types, Case or Example section. The module can
    never MINT one, but the model can author one inside ``description``.
    Since S11 the deposit gate would no longer reject such a row
    (``types_too_early`` is fatal-family, not blocking — it would ship
    flagged), but the no-extraction steer is the Pre lane's own CONTRACT:
    a Types section on a Pre row is a map defect regardless of what a
    later gate would do with it. Catching it here gives the model its
    bounded corrections and then The Fixer's one recorded decision, which
    is where a mid-run defect belongs.
    """

    expected = set(prerequisite_ids)

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        topics = response.get("topics")
        if not isinstance(topics, list):
            return ["response has no topics array"]
        topic_ids = mint_pre_topic_ids(len(topics))
        concepts: list[Mapping[str, Any]] = []
        for position, topic in enumerate(topics):
            if not isinstance(topic, Mapping):
                defects.append("a topic entry is not an object")
                continue
            topic_id = str(topic.get("pre_topic_id") or "")
            if topic_id != topic_ids[position]:
                defects.append(
                    f"topic at position {position + 1} carries id "
                    f"{topic_id or '<empty>'!r}; ids are the positional "
                    f"mint PRT-0001, PRT-0002, … (expected "
                    f"{topic_ids[position]})"
                )
            label = topic_id or topic_ids[position]
            if not _normal(topic.get("title")):
                defects.append(f"{label} has an empty title")
            rows = topic.get("concepts")
            if not isinstance(rows, list):
                defects.append(f"{label} has no concepts array")
                continue
            for entry in rows:
                if isinstance(entry, Mapping):
                    concepts.append(entry)
                else:
                    defects.append(
                        f"{label} carries a concept entry that is not an "
                        "object"
                    )
        concept_ids = mint_pre_concept_ids(len(concepts))
        seen: list[str] = []
        for position, row in enumerate(concepts):
            concept_id = str(row.get("pre_concept_id") or "")
            if concept_id != concept_ids[position]:
                defects.append(
                    f"concept at position {position + 1} carries id "
                    f"{concept_id or '<empty>'!r}; ids are the positional "
                    f"mint PRC-0001, PRC-0002, … in listing order across "
                    f"the whole map (expected {concept_ids[position]})"
                )
            label = concept_id or concept_ids[position]
            for field in ("concept_title", "description", "achieving_mastery"):
                if not _normal(row.get(field)):
                    defects.append(f"{label} has an empty {field}")
            extra = _extra_sections(compose_details(
                row.get("description"), row.get("achieving_mastery")
            ))
            if extra:
                defects.append(
                    f"{label} carries the section(s) "
                    + ", ".join(repr(name) for name in extra)
                    + "; a pre-learning concept ships its description and "
                    "its achieving-mastery sentence and nothing else — no "
                    "Types, no Cases, no Examples, and no question from "
                    "this chapter anywhere in it"
                )
            refs = _ref_list(row.get("prerequisites"))
            if not refs:
                defects.append(
                    f"{label} names no prerequisite_id; every pre-concept "
                    "names the captured prerequisites it teaches"
                )
                continue
            for ref in refs:
                if ref not in expected or ref in seen:
                    defects.append(
                        f"{label} names unknown or repeated "
                        f"prerequisite_id {ref}"
                    )
                    continue
                seen.append(ref)
        missing = sorted(expected - set(seen))
        if missing:
            # R4: every captured prerequisite is mapped exactly once — an
            # unmapped prerequisite is a learner's prerequisite lost, and
            # a repeated one is a double-count.
            defects.append("unmapped prerequisite(s): " + ", ".join(missing))
        return defects

    return check


def _links_checker(
    pre_concept_ids: list[str],
    post_concept_ids: set[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    """Mechanics only: every pre-concept decided once, every link resolves.

    Whether a link is NECESSARY is the critic's advisory dimension, not
    this checker's: a pre-concept may legitimately come back with an
    empty ``post_concept_ids`` list, and that ships flagged (Q10). What
    fails the contract is a link naming a Post concept that does not
    exist (R4: an unresolvable link routes to The Fixer, never dropped).
    """

    expected = set(pre_concept_ids)

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("links")
        if not isinstance(rows, list):
            return ["response has no links array"]
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("a link entry is not an object")
                continue
            pre_id = str(row.get("pre_concept_id") or "")
            if pre_id not in expected or pre_id in seen:
                defects.append(
                    f"unknown or repeated pre_concept_id "
                    f"{pre_id or '<empty>'}"
                )
                continue
            seen.add(pre_id)
            for ref in _ref_list(row.get("post_concept_ids")):
                if ref not in post_concept_ids:
                    defects.append(
                        f"{pre_id} names unknown post concept id {ref}; "
                        "cite only concept ids from the request"
                    )
        missing = sorted(expected - seen)
        if missing:
            defects.append("undecided pre-concept(s): " + ", ".join(missing))
        return defects

    return check


# ---------------------------------------------------------------------------
# live adapters


def _empty_capture_checker(
    response: Mapping[str, Any],
) -> list[str]:
    """Mechanics only: one of the two named verdicts, and a stated reason.

    It does not judge WHICH verdict is right — that is the whole point of
    spending the call — and it bounds nothing by size. It refuses a
    response the release lane could not act on, which is the response
    contract every kernel decision has.
    """

    defects: list[str] = []
    verdict = _normal(response.get("verdict"))
    if verdict not in EMPTY_CAPTURE_VERDICTS:
        defects.append(
            f"verdict {verdict or '<empty>'!r} is not one of "
            + ", ".join(repr(name) for name in EMPTY_CAPTURE_VERDICTS)
        )
    if not _normal(response.get("rationale")):
        defects.append(
            "rationale is empty; a verdict a reviewer cannot read is a "
            "verdict nobody can check"
        )
    return defects


def _live_empty_capture(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PREMAP_EMPTY_CAPTURE_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_empty_capture_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PREMAP_EMPTY_CAPTURE_CRITIC_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _empty_capture_rules(rules_suffix: str) -> str:
    return (
        "Phase 03: this chapter's prerequisite capture came back with no "
        "element at all. Decide which of two things happened, reading the "
        "chapter's own source below. assumes_nothing: this chapter "
        "genuinely opens from nothing a learner at this level, grade, "
        "subject and board must already hold — it teaches its first idea "
        "from the beginning, so there is nothing to place before it. "
        "capture_incomplete: the chapter does assume prior knowledge and "
        "the capture failed to reach it — the source is unreadable, "
        "degraded, truncated or otherwise not what the capture needed. "
        "Answer from the source in front of you: whether the source "
        "READS as a whole chapter, and whether its own teaching leans on "
        "anything it does not itself explain. An honest assumes_nothing "
        "is a legitimate and expected answer for a genuinely opening "
        "chapter and is never a failure; an honest capture_incomplete "
        "costs nothing but a reviewer's attention. rationale states in "
        "one or two sentences what in the source decided it."
        + rules_suffix
    )


def empty_capture_verdict(
    env: Mapping[str, Any],
    qids: list[str],
    *,
    provider: kernel.Provider,
    critic: kernel.Critic | None,
    store: kernel.DecisionStore,
    fixer: kernel.Provider | None,
    rules_suffix: str = "",
) -> dict[str, Any]:
    """ONE recorded verdict on an empty prerequisite capture (D8.3).

    Returns ``{"verdict", "rationale", "review_flags"}``. The critic is
    advisory and never gates (Q10) — ``kernel.decide`` records its dissent
    as review flags on the decision. The Fixer seam is the Q13 route: if
    the model does not positively decide within its bounded corrections,
    ONE recorded flagged best-judgment decision is made and the run
    completes.

    A verdict that is not ``assumes_nothing`` — including a Fixer decision
    that landed on ``capture_incomplete`` — keeps the Pre release
    Diagnostic through ``build_concepts_release.structural_defects``. It
    does not stop the run: finished work always ships, and every download
    stays open.
    """

    payload = {
        "stage": EMPTY_CAPTURE_KIND,
        "rules": _empty_capture_rules(rules_suffix),
        "chapter": chapter_calibration(env),
        "evidence": empty_capture_evidence(env, qids),
    }
    # The same pre-spend post-condition the map payload carries: the
    # chapter's own question identities must not reach a Pre-lane payload.
    _refuse_source_qids(
        payload, qids, where="the Pre empty-capture verdict payload")
    decision = kernel.decide(
        kind=EMPTY_CAPTURE_KIND,
        unit_id="chapter",
        envelope_sha256=str(env.get("envelope_sha256") or ""),
        payload=payload,
        provider=provider,
        checker=_empty_capture_checker,
        critic=critic,
        store=store,
        policy_version=EMPTY_CAPTURE_POLICY_VERSION,
        fixer=fixer,
    )
    response = decision.get("response") or {}
    return {
        "verdict": _normal(response.get("verdict")),
        "rationale": _normal(response.get("rationale")),
        "review_flags": list(decision.get("review_flags") or []),
    }


def _live_map(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PREMAP_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_links(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PREMAP_NEEDED_FOR_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PREMAP_CRITIC_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _map_rules(rules_suffix: str) -> str:
    return (
        "Phase 03: build the chapter's PRE-LEARNING concept map from the "
        "run's captured prerequisite set. A pre-learning concept teaches "
        "a fundamental the chapter assumes the learner already holds — "
        "something taught in a previous year, vocabulary the chapter uses "
        "as if already known, or a basic needed to follow one of its "
        "lines or concepts. Group the captured prerequisites into "
        "concepts, and the concepts into topics, purely by what they "
        "MEAN: prerequisites that one lesson would teach together belong "
        "to one concept, and concepts a teacher would teach in one "
        "sitting belong to one topic. Name EVERY prerequisite_id from "
        "the request exactly once across the whole map — a prerequisite "
        "that fits with no other is its own single-prerequisite concept, "
        "never dropped. How many topics and concepts the captured "
        "evidence supports is entirely your judgment: a chapter that "
        "assumes little yields a small map, and a concept must never be "
        "invented to fill it out or to balance a topic. Author each "
        "concept to the same standard as a post-learning concept, and to "
        "the level, grade, subject and board named in the chapter "
        "calibration: description is the full teaching paragraph for the "
        "prerequisite itself — it teaches the fundamental, in original "
        "language, as it would be taught to a learner about to start "
        "this chapter, and never restates what this chapter goes on to "
        "teach; achieving_mastery is one sentence naming what a learner "
        "can do once that fundamental is held, distinct for every "
        "concept. Carry no 'Description:' or 'Achieving Mastery:' label "
        "inside either field, and no other section: this map has no "
        "Types, no Cases and no Examples, and no question from this "
        "chapter may appear anywhere in it. Wrap every mathematical "
        "expression exactly as [Katex] valid LaTeX [/Katex]. Mint "
        "pre_topic_id positionally as PRT-0001, PRT-0002, … and "
        "pre_concept_id positionally as PRC-0001, PRC-0002, … in listing "
        "order across the whole map. prerequisites is the list of "
        "prerequisite_id values this concept teaches; rationale states "
        "why they are one concept." + rules_suffix
    )


def _links_rules(rules_suffix: str) -> str:
    return (
        "Phase 03: give each PRE-LEARNING concept its explicit "
        "needed-for links — the post-learning concepts of this chapter "
        "that require it. Judge from each post-learning concept's "
        "description against the pre-learning concept's own teaching: "
        "link a post concept when a learner who did NOT hold this "
        "fundamental could not follow that concept's teaching. Cite only "
        "post concept ids from the request, and decide EVERY "
        "pre-learning concept in the request. Link only what genuinely "
        "requires the fundamental — never spread links to cover the "
        "chapter, and never link a concept merely because it mentions "
        "the same subject matter. If nothing in this chapter genuinely "
        "requires a pre-learning concept, return an empty "
        "post_concept_ids list for it and say so in rationale; that is a "
        "legitimate answer and it is reviewed, not corrected away. "
        "rationale: one sentence naming what the linked concepts could "
        "not be understood without." + rules_suffix
    )


# ---------------------------------------------------------------------------
# public entry


def build(
    env: Mapping[str, Any],
    prerequisites: Mapping[str, Any],
    post_rows: list[Mapping[str, Any]] | None = None,
    *,
    provider: kernel.Provider | None = None,
    links_provider: kernel.Provider | None = None,
    analysis_provider: kernel.Provider | None = None,
    analysis_allot_provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
    progress_span: progress.Span | None = None,
) -> dict[str, Any]:
    """Build the Pre-Learning concept map from the merged capture.

    ``progress_span``, when given, is this stage's slice of the progress
    bar (mechanics only): the map decision, the needed-for link batches,
    and the Pre analysis each fill a fixed share of it as they finish, so
    the console bar moves during the Pre lane instead of freezing on the
    stage's opening value.

    ``prerequisites`` is ``prelearn.merge``'s result; ``post_rows`` is
    the run's finished Post rows, used ONLY as link targets (their
    Descriptions), never as build evidence.

    Returns ``{"rows": [Pre concept rows], "topics": [{"pre_topic_id",
    "title", "pre_concept_ids"}], "needed_for": {pre_concept_id:
    [post_concept_id]}, "analysis": {the Pre-scoped Q1 inventory and its
    allotments}, "review_flags": {pre_concept_id: [flags]},
    "decision_flags": {decision: [flags]}, "validation": [findings]}``.
    An empty capture returns an empty map carrying ONE recorded verdict
    under ``pre_lane_verdict`` — ``assumes_nothing`` or
    ``capture_incomplete``, decided by the model against the chapter's own
    source (spec-step8 D8.3). A chapter that genuinely assumes nothing is
    never padded, and a capture that failed is never reported as a chapter
    that needed nothing.
    """
    from . import fixer as fixer_mod
    from . import place as place_mod
    from . import preanalyse as preanalyse_mod
    from . import prompts as prompts_mod
    from .. import concept_refiner as cr

    env = envelope_mod.validate(env)
    qids = inventory_qids(env)
    captured = [
        row for row in (prerequisites or {}).get("prerequisites") or []
        if isinstance(row, Mapping)
        and str(row.get("prerequisite_id") or "").strip()
    ]
    empty: dict[str, Any] = {
        "rows": [],
        "topics": [],
        "needed_for": {},
        "analysis": {
            "inventory": [],
            "allotments": {},
            "rationales": {},
            "review_flags": {},
        },
        "review_flags": {},
        "decision_flags": {},
        "validation": [],
    }
    if not captured:
        # D8.3 / S9 — ONE verdict, not an inference. This branch used to
        # return ``empty`` "without spending a decision", which answered a
        # judgment question ("does this chapter genuinely assume no prior
        # knowledge?") with the shape of a list.
        empty_provider = provider
        empty_critic = critic
        empty_fixer = fixer
        if empty_provider is None:
            envelope_mod.require_live_api()
            empty_provider = _live_empty_capture
            empty_critic = (
                empty_critic if empty_critic is not None
                else _live_empty_capture_critic
            )
            empty_fixer = empty_fixer or fixer_mod.live_fixer
        verdict = empty_capture_verdict(
            env,
            qids,
            provider=empty_provider,
            critic=empty_critic,
            store=store or kernel.DecisionStore(),
            fixer=empty_fixer,
            # Redacted for the same reason as the map path below: a
            # literary chapter's plan slot routes task_qids, and the
            # verdict payload is a Pre-lane payload like any other.
            rules_suffix=_redact_ids(
                prompts_mod.instruction_rules_suffix(
                    env, slots=prompts_mod.PRE_LEARNING_SLOTS
                ),
                qids,
            ),
        )
        # THE FLAGS RIDE THE VERDICT ROW, not only ``decision_flags``.
        # [measured] with the flags in ``decision_flags`` alone, a critic
        # that dissented on THIS decision — the one that opens the Pre
        # lane's zero-row publication — reached the release nowhere at all:
        # ``build_concepts_release`` has no reader for ``decision_flags``
        # (``grep -n decision_flags`` in that module returns nothing), and
        # ``stage_pre_release`` copies this row and this row only. Q10 says
        # dissent is a recorded advisory flag; recorded means recorded
        # where the reviewer of THIS artefact reads it. Carrying them here
        # gets them into the payload, the release issues, the Issues sheet
        # and the diagnostics zip for free, because every one of those
        # already reads this row.
        empty[EMPTY_CAPTURE_VERDICT_FIELD] = {
            "verdict": verdict["verdict"],
            "rationale": verdict["rationale"],
            "review_flags": list(verdict["review_flags"]),
        }
        if verdict["review_flags"]:
            # Kept as well: ``decision_flags`` is the phase-3 convention
            # every other decision in this module writes, and the run's own
            # artifact readers use it.
            empty["decision_flags"]["empty_capture"] = list(
                verdict["review_flags"]
            )
        if progress_span is not None:
            progress_span.tracker(1.0).set_units(
                1.0,
                label="Pre-Learning map: empty-capture verdict recorded",
            )
        progress.log(
            "Pre-Learning map: the run captured no prerequisite element. "
            "The model decided "
            f"{verdict['verdict']!r} against the chapter's own source — "
            "an empty Pre map is never inferred from an empty list, and "
            "never padded to look richer. "
            + verdict["rationale"],
            level=(
                "info" if verdict["verdict"] == ASSUMES_NOTHING else "error"
            ),
        )
        return empty

    analysis_critic = critic
    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_map
        links_provider = links_provider or _live_links
        analysis_provider = analysis_provider or preanalyse_mod._live_build
        analysis_allot_provider = (
            analysis_allot_provider or preanalyse_mod._live_allot
        )
        critic = critic if critic is not None else _live_critic
        # The Pre inventory has its own audit dimensions (genuineness,
        # distinctness, and "is this really about what the chapter goes
        # on to teach"), so it gets its own critic prompt rather than the
        # map critic's four verifications.
        analysis_critic = (
            analysis_critic
            if analysis_critic is not None
            else preanalyse_mod._live_critic
        )
        fixer = fixer or fixer_mod.live_fixer
    links_provider = links_provider or provider
    analysis_provider = analysis_provider or provider
    analysis_allot_provider = analysis_allot_provider or analysis_provider
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    # REDACTED at the mint, like every other channel the Pre lane is
    # shown. The Architect's ``language_topology_plan`` slot (literary
    # chapters) legitimately routes ``task_qids`` — upstream provenance,
    # not a lift — and [job 64, 2026-08-21] carrying it verbatim put the
    # whole chapter inventory into this payload, so the post-condition
    # refused the entire Pre map and both Pre files shipped empty.
    # Expository chapters author no such slot: their suffix has no QID,
    # the redaction is a no-op, and their decision keys do not move.
    rules_suffix = _redact_ids(
        prompts_mod.instruction_rules_suffix(
            env, slots=prompts_mod.PRE_LEARNING_SLOTS
        ),
        qids,
    )

    # ---- the map decision: built from the capture, never from Post ----
    evidence = map_evidence(env, {"prerequisites": captured}, qids)
    payload = {
        "stage": "premap.map",
        "rules": _map_rules(rules_suffix),
        "chapter": chapter_calibration(env),
        "evidence": evidence,
    }
    # The model is never SHOWN a source question's identity: the
    # capture's citations are filtered to block ids and its free text is
    # redacted (``_redact_ids`` records why redaction, not refusal, is
    # the right act on upstream evidence). This is the post-condition of
    # that filtering, checked before any spend — it can only fire if a
    # channel is added here without being filtered.
    _refuse_source_qids(payload, qids, where="the Pre map build payload")
    prerequisite_ids = [
        str(row.get("prerequisite_id") or "") for row in captured
    ]
    decision = kernel.decide(
        kind="premap.map",
        unit_id="chapter",
        envelope_sha256=envelope_sha,
        payload=payload,
        provider=provider,
        checker=_map_checker(prerequisite_ids),
        critic=critic,
        store=store,
        policy_version=POLICY_VERSION,
        fixer=fixer,
    )
    map_flags = list(decision.get("review_flags") or [])
    decision_flags: dict[str, list[str]] = {}
    if map_flags:
        decision_flags["map"] = list(map_flags)

    # Redacted for the same reason the evidence is: this text is COPIED
    # provenance from the upstream capture, not something the Pre lane
    # authored, and it rides ``_aegis_pre_prerequisites`` onto the row
    # itself. Refusing it would brick a finished run on legitimate
    # upstream output through the row guard rather than the payload one
    # — the same permanent failure through a different door.
    text_by_prerequisite = {
        str(row.get("prerequisite_id") or ""): _redact_ids(
            _normal(row.get("text")), qids
        )
        for row in captured
    }
    rows: list[dict[str, Any]] = []
    topics: list[dict[str, Any]] = []
    review_flags: dict[str, list[str]] = {}
    authored = [
        topic for topic in decision["response"].get("topics") or []
        if isinstance(topic, Mapping)
    ]
    topic_ids = mint_pre_topic_ids(len(authored))
    concept_ids = mint_pre_concept_ids(sum(
        len([
            entry for entry in topic.get("concepts") or []
            if isinstance(entry, Mapping)
        ])
        for topic in authored
    ))
    position = 0
    for topic_id, topic in zip(topic_ids, authored):
        title = _normal(topic.get("title"))
        member_ids: list[str] = []
        for entry in topic.get("concepts") or []:
            if not isinstance(entry, Mapping):
                continue
            concept_id = concept_ids[position]
            position += 1
            member_ids.append(concept_id)
            refs = [
                ref for ref in _ref_list(entry.get("prerequisites"))
                if ref in text_by_prerequisite
            ]
            # The house format, minted exactly as settle.py mints it for
            # a Post row: Description + Achieving Mastery, and nothing
            # else. There is no code path here that can add a Types,
            # Case, Example or analysis section.
            details = kr.repair_unwrapped_math(compose_details(
                entry.get("description"), entry.get("achieving_mastery")
            ))
            row = {
                "topic": title,
                "parent_concept": "",
                "concept_title": _normal(entry.get("concept_title")),
                "concept_details": details,
                "keywords": _normal(entry.get("keywords")),
                "_semantic_topic_id": topic_id,
                # A Pre concept has no current-chapter grounding by
                # definition (spec T4); the contract says so explicitly
                # rather than leaving an empty list to be read as a bug.
                "_source_block_ids": [],
                "_source_grounding_contract": PRE_GROUNDING_CONTRACT,
                "_pre_concept_id": concept_id,
                PREREQUISITES_FIELD: [
                    {
                        "prerequisite_id": ref,
                        "text": text_by_prerequisite[ref],
                    }
                    for ref in refs
                ],
                NEEDED_FOR_FIELD: [],
            }
            # A flag naming one pre-concept lands on that row only; a
            # genuinely map-wide flag reaches every row (kernel.pin_flags
            # — settle's staging fix).
            pinned = kernel.pin_flags(
                list(map_flags), list(concept_ids), str(concept_id)
            )
            if pinned:
                review_flags[concept_id] = pinned
            rows.append(row)
        topics.append({
            "pre_topic_id": topic_id,
            "title": title,
            "pre_concept_ids": member_ids,
        })

    # Three equal-weight units: the map decision (done here), the
    # needed-for link batches, and the Pre analysis. Unit weights are a
    # fixed mechanical allocation, not a duration estimate.
    span_tracker = (
        progress_span.tracker(3.0) if progress_span is not None else None
    )
    if span_tracker is not None:
        span_tracker.set_units(
            1.0,
            label=(
                f"Pre-Learning map: {len(rows)} pre-concept(s) across "
                f"{len(topics)} pre-topic(s) authored"
            ),
        )
    progress.log(
        f"Pre-Learning map: {len(rows)} pre-concept(s) across "
        f"{len(topics)} pre-topic(s), built from the captured "
        "prerequisite set (model-judged; a thin capture is never padded)."
    )

    # ---- needed-for links: reading Post Descriptions to LINK to them --
    post_rows = list(post_rows or [])
    post_ids = place_mod.mint_concept_ids(post_rows)
    post_payload = [
        {
            "concept_id": concept_id,
            "concept_title": _redact_ids(
                _normal(row.get("concept_title")), qids
            ),
            # Redacted for the same reason the capture's text is: a Post
            # Description is evidence the Pre lane is SHOWN, and a run
            # that reached this point has a finished Post map to ship.
            "description": _redact_ids(
                _description_of(row.get("concept_details")), qids
            ),
        }
        for concept_id, row in zip(post_ids, post_rows)
        if not cr.is_culmination(_normal(row.get("concept_title")))
    ]
    title_by_id = {
        row["concept_id"]: row["concept_title"] for row in post_payload
    }
    needed_for: dict[str, list[str]] = {}
    if rows and post_payload:
        known_post_ids = set(title_by_id)
        pre_payload = [
            {
                "pre_concept_id": row["_pre_concept_id"],
                "concept_title": row["concept_title"],
                "description": _description_of(row["concept_details"]),
            }
            for row in rows
        ]

        def _decide_links(start: int):
            batch = pre_payload[start:start + _LINK_BATCH_SIZE]
            request = {
                "stage": "premap.needed_for",
                "rules": _links_rules(rules_suffix),
                "chapter": chapter_calibration(env),
                "pre_concepts": batch,
                "post_concepts": post_payload,
            }
            _refuse_source_qids(
                request, qids, where="the needed-for link payload"
            )
            link_decision = kernel.decide(
                kind="premap.needed_for",
                unit_id=f"concepts#{start}",
                envelope_sha256=envelope_sha,
                payload=request,
                provider=links_provider,
                checker=_links_checker(
                    [entry["pre_concept_id"] for entry in batch],
                    known_post_ids,
                ),
                critic=critic,
                store=store,
                policy_version=POLICY_VERSION,
                fixer=fixer,
            )
            decided = {
                str(link.get("pre_concept_id") or ""): link
                for link in link_decision["response"].get("links") or []
                if isinstance(link, Mapping)
            }
            flags = list(link_decision.get("review_flags") or [])
            return start, [
                (entry["pre_concept_id"], decided[entry["pre_concept_id"]])
                for entry in batch
            ], flags

        workers = config.phase3_decision_workers()
        link_batches = list(range(0, len(pre_payload), _LINK_BATCH_SIZE))
        for start, decided_batch, flags in kernel.parallel_map_in_order(
            link_batches,
            _decide_links,
            max_workers=workers,
            on_result=(
                None if span_tracker is None
                else lambda index, item, result: span_tracker.advance(
                    1.0 / max(1, len(link_batches)),
                    label=(
                        "Pre-Learning map: needed-for links "
                        f"{index + 1}/{len(link_batches)} decided"
                    ),
                )
            ),
        ):
            if flags:
                decision_flags[f"needed_for#{start}"] = list(flags)
            batch_pre_ids = [
                str(batch_pre_id) for batch_pre_id, _v in decided_batch
            ]
            for pre_id, verdict in decided_batch:
                needed_for[pre_id] = _ref_list(
                    verdict.get("post_concept_ids")
                )
                pinned = kernel.pin_flags(
                    list(flags), batch_pre_ids, str(pre_id)
                )
                if pinned:
                    review_flags.setdefault(pre_id, []).extend(pinned)

    by_pre_id = {row["_pre_concept_id"]: row for row in rows}
    for pre_id, row in by_pre_id.items():
        links = needed_for.get(pre_id, [])
        row[NEEDED_FOR_FIELD] = [
            {
                "post_concept_id": concept_id,
                "post_concept_title": title_by_id.get(concept_id, ""),
            }
            for concept_id in links
        ]
        if not links:
            # Necessity is the critic's advisory dimension (Q10): a
            # pre-concept nothing in this chapter requires still ships,
            # and the reviewer is told. It is never dropped and never
            # retried.
            review_flags.setdefault(pre_id, []).append(
                "no post-learning concept of this chapter was linked as "
                "requiring this pre-learning concept; its necessity needs "
                "review (the concept ships either way)"
            )

    # ---- the Pre lane's own Q1 inventory, and its allotted sections ---
    #
    # Q1 reaches this lane through an inventory of its OWN
    # (``preanalyse.py``, own kinds and own POLICY_VERSION): the Post
    # pass cannot be pointed at these rows — its evidence is the current
    # chapter's source blocks and question inventory, its allot target
    # set is the Post ordering, and widening either would re-key every
    # stored Post analysis decision. The section exists only where an
    # item was allotted, NOT every pre-concept receives one, and
    # Achieving Mastery (minted above, inside the Description section)
    # is untouched by any of it.
    #
    # It runs HERE — after the rows exist, before the guard — so the
    # stamped item text is inside the surface ``_refuse_source_qids``
    # scans, and inside the seal that follows.
    if span_tracker is not None:
        span_tracker.set_units(
            2.0, label="Pre-Learning map: needed-for links decided"
        )
    analysis = preanalyse_mod.analyse(
        env,
        {"prerequisites": captured},
        rows,
        qids=qids,
        rules_suffix=rules_suffix,
        provider=analysis_provider,
        allot_provider=analysis_allot_provider,
        critic=analysis_critic,
        store=store,
        fixer=fixer,
    )
    for pre_id, flags in preanalyse_mod.stamp(rows, analysis).items():
        review_flags.setdefault(pre_id, []).extend(flags)
    if span_tracker is not None:
        span_tracker.set_units(
            3.0, label="Pre-Learning map: analysis allotted"
        )

    # ---- the fail-closed guard, then advisory validation, then the seal
    #
    # The guard runs over the AUTHORED artefact and it runs FIRST, before
    # any flag is stamped onto a row. Two reasons, both load-bearing:
    #
    # * ``topics`` is guarded, not just ``rows``. A topic the model
    #   authored with no concepts under it never becomes a row, so a QID
    #   in its title would slip past a rows-only scan and still ride the
    #   ``pre_map`` carry channel and its snapshot — inside the very
    #   artefact the steer names ("a Pre row or the Pre release
    #   payload");
    # * review flags are NOT part of the guarded surface, and the
    #   ordering is what keeps them out. A flag is auditor prose ABOUT
    #   the artefact, not content lifted into it — and the critic is
    #   asked in as many words whether a concept carries "its questions",
    #   so a critic answering that in the most useful way, by naming the
    #   question, would otherwise turn its own advisory dissent into the
    #   hardest gate in the pipeline: the verdict is stored
    #   content-addressed, so the refusal would replay forever at zero
    #   spend. Q10 says a critic's dissent is "an advisory review flag,
    #   never a gate"; scanning before stamping is how that stays true.
    # * ``analysis`` is guarded for exactly the reason ``topics`` is. An
    #   inventory item's ``rationale``, and an allotment's, never reach a
    #   row — but they ride the same ``pre_map`` carry channel and the
    #   same snapshot, which is the artefact the steer names. Its
    #   ``review_flags`` are excluded by the SAME Q10 rule as the row
    #   flags above: the Pre-analysis critic is asked about a
    #   prerequisite's misconceptions, and a critic that names a source
    #   question in its dissent must not become a permanent, replaying
    #   gate over work that is already paid for.
    _refuse_source_qids(
        {
            "rows": rows,
            "topics": topics,
            "needed_for": needed_for,
            "analysis": {
                key: value
                for key, value in analysis.items()
                if key != "review_flags"
            },
        },
        qids,
        where="a Pre-Learning concept row",
    )

    validation = validate_rows(rows)
    for finding in validation:
        try:
            index = int(finding.get("row_index"))
        except (TypeError, ValueError):
            index = -1
        if 0 <= index < len(rows):
            pre_id = rows[index]["_pre_concept_id"]
            review_flags.setdefault(pre_id, []).append(
                f"Pre validator [{finding.get('code')}]: "
                f"{finding.get('message')}"
            )
    for row in rows:
        flags = review_flags.get(row["_pre_concept_id"]) or []
        if flags:
            row["review_flags"] = list(flags)

    seal(env, rows)

    progress.log(
        f"Pre-Learning map: every one of {len(prerequisite_ids)} captured "
        "prerequisite(s) mapped exactly once (R4); needed-for links "
        f"decided for {len(needed_for)} pre-concept(s).",
        level="success",
    )
    return {
        "rows": rows,
        "topics": topics,
        "needed_for": needed_for,
        "analysis": analysis,
        "review_flags": review_flags,
        "decision_flags": decision_flags,
        "validation": validation,
    }


def validate_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Run the shared concept validator under the Pre lane's stance.

    Advisory here by design: findings are stamped on their row as review
    flags, nothing is dropped and nothing raises. ``description_length``
    and ``thin_description`` collide by design with a concise
    prerequisite description, so promoting Pre warnings to fatals
    wholesale would fail exactly the rows the format asks for.

    Q1's allotment context is DERIVED from the rows themselves (the
    ``_aegis_analysis_allotments`` marker ``preanalyse.stamp`` writes),
    never assumed: only a row the Pre inventory allotted an item to may
    carry the Misconception/ Error Analysis section, and a section on any
    other row is named by ``unallotted_analysis_section``. Marker
    accounting, no content judgment.
    """
    from .. import concept_validator

    prepared = [dict(row) for row in rows]
    flags = dict(PRE_VALIDATOR_FLAGS)
    flags["analysis_allotted_keys"] = concept_validator.analysis_allotted_keys(
        prepared
    )
    report = concept_validator.validate_concept_rows(prepared, **flags)
    return [dict(finding) for finding in report.get("errors") or []]


def seal(
    env: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Seal Pre rows on the prerequisite-capture grounding contract.

    The same row seal every Post row gets, minus exactly one thing: the
    current-chapter block check, which a Pre row cannot satisfy and must
    not (spec T4). ``grounding_certificate`` makes that exclusion narrow
    and two-sided — a row on this contract carrying a chapter block id
    fails the seal.
    """
    from . import assemble as assemble_mod
    from .. import grounding_certificate

    for row in rows:
        row.setdefault("_source_grounding_contract", PRE_GROUNDING_CONTRACT)
        row["_source_grounding_version"] = assemble_mod.GROUNDING_VERSION
    return grounding_certificate.seal_records(
        rows,
        source_contract_hash=str(env.get("source_contract_hash") or ""),
        semantic_topology_sha256=(
            grounding_certificate.semantic_topology_sha256(env["graph"])
        ),
        allowed_block_ids={
            str(row.get("block_id") or "")
            for row in env["graph"]["blocks"]
            if isinstance(row, Mapping) and str(row.get("block_id") or "")
        },
    )
