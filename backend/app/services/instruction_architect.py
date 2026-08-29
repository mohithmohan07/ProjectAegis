"""The Architect — builds the run's instruction set (docs/aegis-restructure.md §8.1).

At run start, once the chapter is resolved, the Architect assembles the
complete instruction set for this specific chapter. The **frozen core** —
working rules, placement rules, house formats, output contracts, naming
conventions, the no-local-fallback invariant — is never the Architect's to
rewrite: it stays in the registered prompts (admin-overridable) and the
Phase 3 module constants. The Architect authors ONLY the variable slots:

* ``subject_topology_guidance`` — seeded from the subject adapter
  (:func:`canonical_source_phase3.subject_adapter`);
* ``grade_band_vocabulary`` — vocabulary calibration for the grade band;
* ``language_mode`` — poem | prose | expository selection with rationale
  (selection only; the poem/prose topology adapter is a later build step);
* ``board_publication_conventions`` — seeded from the board metadata and
  the upload's ``source_book`` publication label when present;
* ``publication_label`` — the publication identity the source's own
  cover/imprint/front-matter text evidences (e.g. ``Balbharati``), captured
  so a blank upload ``source_book`` can be filled from the source itself
  rather than from the uploaded PDF's filename (owner audit 2026-08-27,
  A3/A4);
* ``chapter_cautions`` — chapter-specific hazards read from the source.

Governance: the assembled set is versioned and ``instruction_set_sha256``
joins every decision key (envelope seal and reuse triple, kernel decision
keys via the sealed envelope, checkpoint fingerprints and their validator
mirror, ``semantic_context_hash``, both pre-spend human-gate identities),
so a changed instruction set can never silently reuse old verdicts. An
independent critic reviews the assembly — advisory only, its dissent lands
in ``review_flags`` and never gates (Q10). Assembly failure fails closed:
this is pre-spend configuration, before generation begins, so The Fixer
(§8.2, mid-run blocks only) deliberately does not apply here. The set is
persisted to ``<artifact_dir>/source.instruction-set.json`` and therefore
ships in the release diagnostics automatically.

Frozen-core hash material (see :func:`_frozen_core_entries`): the
registered prompt keys generation actually reads during a Build Concepts
run, plus the enumerated Phase 3 system-prompt module constants hashed
directly — ``app/services/phase3/prompts.py`` bypasses the prompt registry
today, so its constants are hashed from the module rather than through the
registry. The enumeration is explicit and is not yet every constant in that
module (see ``_FROZEN_CORE_PHASE3_CONSTANTS``).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import config
from . import prompts

# ``architect-2`` adds the ``publication_label`` slot (owner audit
# 2026-08-27, A3/A4: source identity is the publication, never a filename).
ARCHITECT_VERSION = "architect-2"

INSTRUCTION_SET_FILENAME = "source.instruction-set.json"

_ALLOWED_LANGUAGE_MODES = ("poem", "prose", "expository")

# Registered prompt keys a Build Concepts run actually reads (generation.py
# ``prompts.get_text``/``prompts.render`` call sites, plus the Pass 1 / Pass 4
# contracts installed around it). Every text is admin-overridable; an override
# changes the frozen-core hash and therefore the instruction-set hash, which
# invalidates every cached verdict made under the previous instructions.
_FROZEN_CORE_PROMPT_KEYS = (
    "chapter_reading.normalize.system",
    "concepts.canonicalize.system",
    "concepts.chapter_meta.system",
    "concepts.chapter_wide_task_topics.system",
    "concepts.concept_type_sufficiency.system",
    "concepts.culmination.system",
    "concepts.method_anchor_recovery.system",
    "concepts.method_worked_example.system",
    "concepts.missing_topic_recovery.system",
    "concepts.opening_recovery.system",
    "concepts.question_polishing.system",
    "concepts.question_task_inventory.system",
    "concepts.repair.system",
    "concepts.skeleton.system",
    "concepts.task_fragment_consolidation.system",
    "concepts.topic_segregation_verdict.system",
    "concepts.topic_structure.system",
    "concepts.type_granularity_critic.system",
    "concepts.type_host_review.system",
    "concepts.type_mining.system",
    "concepts.type_mining_delta.system",
    "concepts.type_semantic_consolidation.system",
    # Re-keyed 2026-08-29 (owner decisions D1/P10): the preamble moved to a
    # fresh registry key so a stale Admin override cannot resurrect the
    # superseded rules; the key change re-hashes the instruction set, which
    # correctly invalidates verdicts cached under the old rules.
    "content.katex_rules.v2",
)

# Phase 3 system prompts are module constants (no registry backing yet); they
# are hashed directly from the module, in this order. A constant listed here
# re-keys every cached verdict when its text changes, which is the point: the
# prompt that decides a pass's output is part of the instructions that pass
# decided under. Names are appended, never reordered.
#
# The Pre lane's capture, map, inventory and question prompts were added
# together, in the same commit that first moved this hash for the question
# pass - one re-key rather than two. Before that, editing PREMAP_* or
# PREANALYSE_* replayed old verdicts, which was a real cache-invalidation
# defect in the slices that introduced them.
#
# The six Post-lane prompts below closed that gap in step 8 (§10 line 726),
# appended together in one commit so the Post lane re-keys once rather than
# six times. Until then, editing ANALYSE_*, PLACE_* or REFINER_SYSTEM replayed
# verdicts made under the old text: a real cache-invalidation defect, deferred
# rather than unnoticed, because listing them re-keys every stored Post
# decision and the Pre-lane slices that found it had no business spending that
# blast radius. Nothing is absent from this tuple now; a phase-3 system prompt
# added later belongs here in the same commit that adds it.
_FROZEN_CORE_PHASE3_CONSTANTS = (
    "TOPOLOGY_SYSTEM",
    "GROUNDING_SYSTEM",
    "ANALYSIS_SYSTEM",
    "HOST_SYSTEM",
    # Q14 — the Type-ownership consolidation verdict.
    "TYPE_OWNER_SYSTEM",
    "POLISH_SYSTEM",
    "CRITIC_SYSTEM",
    "FIXER_SYSTEM",
    "PRELEARN_CAPTURE_SYSTEM",
    "PRELEARN_MERGE_SYSTEM",
    "PRELEARN_CRITIC_SYSTEM",
    "PREMAP_SYSTEM",
    "PREMAP_NEEDED_FOR_SYSTEM",
    "PREMAP_CRITIC_SYSTEM",
    # spec-step8 S9 — the empty-capture verdict and its advisory critic.
    "PREMAP_EMPTY_CAPTURE_SYSTEM",
    "PREMAP_EMPTY_CAPTURE_CRITIC_SYSTEM",
    "PREANALYSE_INVENTORY_SYSTEM",
    "PREANALYSE_ALLOT_SYSTEM",
    "PREANALYSE_CRITIC_SYSTEM",
    "PREQUESTIONS_PLAN_SYSTEM",
    "PREQUESTIONS_AUTHOR_SYSTEM",
    "PREQUESTIONS_CRITIC_SYSTEM",
    "ANALYSE_INVENTORY_SYSTEM",
    "ANALYSE_ALLOT_SYSTEM",
    "ANALYSE_CRITIC_SYSTEM",
    "PLACE_SYSTEM",
    "PLACE_CRITIC_SYSTEM",
    "REFINER_SYSTEM",
)


class InstructionAssemblyError(RuntimeError):
    """The Architect could not assemble a valid instruction set.

    Raised pre-spend, before generation begins: the run must not start
    under invented or partial instructions, and The Fixer (mid-run blocks
    only) does not apply here. Stopping here is recoverable; generating a
    chapter under silently-guessed instructions is not.
    """


ARCHITECT_SYSTEM = prompts.register(
    "architect.assemble.system",
    label="Architect — instruction-set assembly",
    category="Architect",
    default=(
        "You are The Architect of Aegis, an unattended concept-extraction "
        "pipeline for school textbooks (docs/aegis-restructure.md §8.1). At "
        "run start you author ONLY the variable slots of this run's "
        "instruction set. The frozen core — working rules, placement rules, "
        "house formats, output contracts, naming conventions, and the "
        "no-local-fallback invariant — is not yours to rewrite and reaches "
        "every phase unchanged. Respond with a single JSON object and "
        "nothing else, exactly this schema: {\"subject_topology_guidance\": "
        "\"...\", \"grade_band_vocabulary\": \"...\", \"language_mode\": "
        "{\"mode\": \"poem|prose|expository\", \"rationale\": \"...\"}, "
        "\"board_publication_conventions\": \"...\", "
        "\"publication_label\": \"...\", \"chapter_cautions\": "
        "[\"...\"]}. Author each slot as a short, concrete text block "
        "grounded ONLY on the metadata and source text in the request — "
        "never invent facts about the board, publication, or chapter that "
        "the request does not support; an empty string (or empty list) is "
        "the correct answer when the source gives you nothing specific to "
        "say. subject_topology_guidance: how topics and concepts are "
        "organised for this subject (the request names the subject "
        "adapter) and how to read THIS chapter's topology — never generic "
        "study advice. grade_band_vocabulary: vocabulary and "
        "sentence-complexity calibration for this grade band so authored "
        "content reads at the learners' level. language_mode: select the "
        "mode that matches how the source teaches — 'poem' for "
        "stanza-structured literature, 'prose' for story or narrative "
        "literature, 'expository' for everything else (the default for "
        "non-literature chapters) — and state the evidence in rationale. "
        "You SELECT and record the mode only; its topology adapter is "
        "implemented elsewhere. board_publication_conventions: "
        "terminology, notation, and exercise-structure conventions of this "
        "board and publication that the metadata and source actually "
        "evidence. publication_label: the short publication/publisher "
        "identity the source's own cover, imprint, or front-matter text "
        "names (e.g. 'Balbharati', 'NCERT') — the label a teacher would "
        "cite as the book's source. Use the metadata's source_book when it "
        "already names one; otherwise read it from the source text itself. "
        "Never derive it from a filename, and empty string when neither "
        "the metadata nor the source names a publication. "
        "chapter_cautions: chapter-specific hazards you can "
        "point to in the source (figure-dependent tasks, unusual "
        "numbering, mixed languages, scanned-table artifacts, and the "
        "like), one short caution per entry. Decide every slot; never "
        "defer. Invented conventions poison every later phase."
    ),
    description=(
        "System prompt for The Architect's one instruction-set assembly "
        "call at run start. It authors only the variable slots; the frozen "
        "core stays in the other registered prompts and the Phase 3 "
        "constants."
    ),
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _frozen_core_entries() -> list[dict[str, str]]:
    """Hash the frozen-core prompt texts as they stand right now.

    Registry-backed keys hash through :func:`prompts.get_text`, so an admin
    override participates (the admin registry is the human override for the
    frozen core). Phase 3 constants hash directly from the module.
    """

    # Imports guarantee the registrations exist before get_text runs; the
    # modules are already imported in any running app (services __init__).
    from . import chapter_reading, generation, katex_rules, question_polishing
    from .phase3 import prompts as phase3_prompts

    registration_modules = (
        chapter_reading,  # registers chapter_reading.normalize.system
        generation,  # registers the concepts.* keys
        katex_rules,  # registers content.katex_rules.v2
        question_polishing,  # registers concepts.question_polishing.system
    )
    assert all(registration_modules)

    entries = [
        {"key": key, "sha256": _sha256_text(prompts.get_text(key))}
        for key in _FROZEN_CORE_PROMPT_KEYS
    ]
    entries.extend(
        {
            "key": f"phase3:{name}",
            "sha256": _sha256_text(getattr(phase3_prompts, name)),
        }
        for name in _FROZEN_CORE_PHASE3_CONSTANTS
    )
    return entries


def _empty_slots() -> dict[str, Any]:
    """Deterministic EMPTY slots for the offline path: mechanics only.

    No invented guidance — every authored text is empty, so offline corpus
    runs and tests stay byte-deterministic while still carrying a real,
    stable instruction-set hash over the frozen core.
    """

    return {
        "subject_topology_guidance": "",
        "grade_band_vocabulary": "",
        "language_mode": {"mode": "", "rationale": ""},
        "board_publication_conventions": "",
        "publication_label": "",
        "chapter_cautions": [],
    }


def instruction_set_sha256(
    slots: Mapping[str, Any],
    frozen_core: list[dict[str, str]],
    *,
    architect_version: str = ARCHITECT_VERSION,
) -> str:
    """The governance hash: version + authored slots + frozen-core hashes."""

    material = {
        "architect_version": str(architect_version),
        "slots": dict(slots),
        "frozen_core": list(frozen_core),
    }
    return _sha256_text(_canonical_json(material))


def empty_set_sha256() -> str:
    """Hash of the empty (offline-defaults) instruction set, as frozen now.

    This is the null Architect assembly: no authored slot text, current
    frozen core. It is the default instruction identity for run paths that
    do not assemble (standalone transformations) and for checkpoints written
    before the field existed — so a frozen-core prompt change still
    invalidates those fingerprints, which is the point.
    """

    return instruction_set_sha256(_empty_slots(), _frozen_core_entries())


def _identity_metadata(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    from . import canonical_source_phase3 as phase3_core

    metadata = dict(metadata or {})
    identity = {
        key: ("" if metadata.get(key) is None else str(metadata.get(key)))
        for key in (
            "board", "grade", "subject", "unit", "chapter_title",
            "chapter_id", "chapter_code", "learning_kind", "source_book",
        )
    }
    identity["subject_adapter"] = phase3_core.subject_adapter(
        identity["subject"]
    )
    return identity


def _slot_defects(value: Any) -> list[str]:
    """Mechanical response-schema validation only — no content judgment."""

    if not isinstance(value, Mapping):
        return ["response is not a JSON object"]
    defects: list[str] = []
    for key in (
        "subject_topology_guidance",
        "grade_band_vocabulary",
        "board_publication_conventions",
    ):
        if not isinstance(value.get(key), str):
            defects.append(f"{key} must be a string")
    # Absent means "the source names no publication" — the documented
    # empty answer — so only a present, non-string value is a defect.
    if not isinstance(value.get("publication_label", ""), str):
        defects.append("publication_label must be a string when present")
    mode = value.get("language_mode")
    if not isinstance(mode, Mapping):
        defects.append("language_mode must be an object")
    else:
        if str(mode.get("mode") or "").strip() not in _ALLOWED_LANGUAGE_MODES:
            defects.append(
                "language_mode.mode must be one of "
                + "|".join(_ALLOWED_LANGUAGE_MODES)
            )
        if not isinstance(mode.get("rationale", ""), str):
            defects.append("language_mode.rationale must be a string")
    cautions = value.get("chapter_cautions")
    if not isinstance(cautions, list) or any(
        not isinstance(row, str) for row in cautions
    ):
        defects.append("chapter_cautions must be an array of strings")
    return defects


def _normalized_slots(response: Mapping[str, Any]) -> dict[str, Any]:
    mode = response.get("language_mode") or {}
    return {
        "subject_topology_guidance": str(
            response.get("subject_topology_guidance") or ""
        ).strip(),
        "grade_band_vocabulary": str(
            response.get("grade_band_vocabulary") or ""
        ).strip(),
        "language_mode": {
            "mode": str(mode.get("mode") or "").strip(),
            "rationale": str(mode.get("rationale") or "").strip(),
        },
        "board_publication_conventions": str(
            response.get("board_publication_conventions") or ""
        ).strip(),
        "publication_label": str(
            response.get("publication_label") or ""
        ).strip(),
        "chapter_cautions": [
            str(row).strip()
            for row in response.get("chapter_cautions") or []
            if str(row).strip()
        ],
    }


def _finalize(
    identity: dict[str, str],
    slots: dict[str, Any],
    frozen_core: list[dict[str, str]],
    *,
    slots_source: str,
    review_flags: list[str],
) -> dict[str, Any]:
    return {
        "architect_version": ARCHITECT_VERSION,
        "metadata": identity,
        "slots": slots,
        "slots_source": slots_source,
        "frozen_core_registry_keys": [row["key"] for row in frozen_core],
        "frozen_core": frozen_core,
        "review_flags": list(review_flags),
        "instruction_set_sha256": instruction_set_sha256(slots, frozen_core),
    }


def assemble_instruction_set(
    *,
    metadata: Mapping[str, Any] | None,
    source_text: str,
    api_call: Callable[..., Mapping[str, Any]] | None = None,
    critic: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the run's instruction set: one model call, one critic review.

    Offline (``AEGIS_USE_LIVE=0``): returns the deterministic
    offline-defaults set — EMPTY slots, ``slots_source:
    "offline-defaults"`` — with no model calls, so tests and offline corpus
    runs work byte-deterministically. Live: one JSON-schema model call
    authors the slots; an invalid slot set raises
    :class:`InstructionAssemblyError` (fail closed, pre-spend — slots are
    NEVER invented locally). The independent critic's dissent lands in
    ``review_flags`` and never gates (Q10).
    """

    identity = _identity_metadata(metadata)
    frozen_core = _frozen_core_entries()

    if not config.use_live_generation():
        return _finalize(
            identity,
            _empty_slots(),
            frozen_core,
            slots_source="offline-defaults",
            review_flags=[],
        )

    from . import generation
    from .phase3 import kernel as p3_kernel
    from .phase3 import prompts as phase3_prompts

    call = api_call or generation._openai_json
    request = {
        "task": (
            "Assemble this run's variable instruction slots for the chapter "
            "below. Ground every slot on the metadata and source_text only."
        ),
        "metadata": identity,
        "source_text": str(source_text or ""),
    }
    response = call(
        prompts.get_text("architect.assemble.system"),
        json.dumps(request, ensure_ascii=False, indent=1),
        purpose="metadata",
    )
    defects = _slot_defects(response)
    if defects:
        raise InstructionAssemblyError(
            "The Architect's assembly response failed its mechanical slot "
            "schema; refusing to invent instruction slots (pre-spend fail "
            "closed): " + "; ".join(defects[:8])
        )
    slots = _normalized_slots(response)

    review_flags: list[str] = []
    critic_fn = critic
    if critic_fn is None:
        def critic_fn(payload: dict[str, Any]) -> Mapping[str, Any]:
            return call(
                phase3_prompts.CRITIC_SYSTEM,
                json.dumps(payload, ensure_ascii=False, indent=1),
                purpose="metadata",
            )

    try:
        review = critic_fn({
            "stage": "architect_review",
            "metadata": identity,
            "source_text": str(source_text or ""),
            "proposed_decision": slots,
        })
    except Exception as exc:  # the auditor can never take the run down
        review = None
        review_flags.append(
            f"critic failed to run ({type(exc).__name__}); instruction "
            "assembly stands unaudited"
        )
    if review is not None:
        review_flags.extend(p3_kernel.advisory_flags(review))

    return _finalize(
        identity,
        slots,
        frozen_core,
        slots_source="model",
        review_flags=review_flags,
    )


def write_instruction_set(
    instruction_set: Mapping[str, Any], artifact_dir: str | Path
) -> Path:
    """Persist the assembled set into the artifact directory.

    The release diagnostics zip mirrors the whole artifact directory, so
    the set ships for replay automatically.
    """

    directory = Path(artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / INSTRUCTION_SET_FILENAME
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(instruction_set), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def load_instruction_set(artifact_dir: str | Path) -> dict[str, Any] | None:
    path = Path(artifact_dir) / INSTRUCTION_SET_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _stored_set_reusable(
    stored: Mapping[str, Any] | None,
    frozen_core: list[dict[str, str]],
) -> bool:
    """Decide-once on the authored slots: replay the persisted assembly.

    A stored set replays only while its frozen core matches the current
    frozen core exactly and its own hash verifies — an admin prompt change
    (or tampered artifact) forces a fresh assembly, whose new hash then
    invalidates every cached verdict downstream.
    """

    if not isinstance(stored, Mapping):
        return False
    if str(stored.get("architect_version") or "") != ARCHITECT_VERSION:
        return False
    slots = stored.get("slots")
    if _slot_defects(slots):
        return False
    if list(stored.get("frozen_core") or []) != frozen_core:
        return False
    return str(stored.get("instruction_set_sha256") or "") == (
        instruction_set_sha256(dict(slots), frozen_core)
    )


def ensure_instruction_set(
    *,
    metadata: Mapping[str, Any] | None,
    source_text: str,
    artifact_dir: str | Path | None = None,
    api_call: Callable[..., Mapping[str, Any]] | None = None,
    critic: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble (or replay) the run's instruction set and persist it.

    Live resumes replay the persisted assembly (decide-once) while the
    frozen core is unchanged, so a retry keeps the exact instruction
    identity its checkpoints and decisions were made under. Offline always
    recomputes the deterministic offline-defaults set.
    """

    if config.use_live_generation() and artifact_dir is not None:
        stored = load_instruction_set(artifact_dir)
        if _stored_set_reusable(stored, _frozen_core_entries()):
            return dict(stored)
    built = assemble_instruction_set(
        metadata=metadata,
        source_text=source_text,
        api_call=api_call,
        critic=critic,
    )
    if artifact_dir is not None:
        write_instruction_set(built, artifact_dir)
    return built
