"""Shared service-level contract registration."""

from . import generation as generation
from .closed_inventory_contract import install as _install_closed_inventory_contract
from .concept_topology_contract import install as _install_concept_topology_contract
from .concept_topology_compat import install as _install_concept_topology_compat
from .source_visual_contract import install as _install_source_visual_contract
from .terminal_figure_contract import install as _install_terminal_figure_contract
from .canonical_source_contract import install as _install_canonical_source_contract
from .canonical_source_phase2_contract import (
    install as _install_canonical_source_phase2_contract,
)
from .canonical_source_phase2_compat import (
    install as _install_canonical_source_phase2_compat,
)
from .canonical_source_phase21_contract import (
    install as _install_canonical_source_phase21_contract,
)
from .canonical_source_phase211_contract import (
    install as _install_canonical_source_phase211_contract,
)
from .canonical_source_phase22_contract import (
    install as _install_canonical_source_phase22_contract,
)
from .canonical_source_phase221_contract import (
    install as _install_canonical_source_phase221_contract,
)
from .canonical_source_phase222_contract import (
    install as _install_canonical_source_phase222_contract,
)
from .canonical_source_phase3_contract import (
    install as _install_canonical_source_phase3_contract,
)
from .canonical_source_phase31_grounding_contract import (
    install as _install_canonical_source_phase31_grounding_contract,
)
from .canonical_source_phase32_topology_adjudication_contract import (
    install as _install_canonical_source_phase32_topology_adjudication_contract,
)
from .canonical_source_phase33_preflight_contract import (
    install as _install_canonical_source_phase33_preflight_contract,
)
from .canonical_source_phase331_host_authority_contract import (
    install as _install_canonical_source_phase331_host_authority_contract,
)
from .canonical_source_phase332_cache_compat_contract import (
    install as _install_canonical_source_phase332_cache_compat_contract,
)
from .canonical_source_phase333_multitopic_host_contract import (
    install as _install_canonical_source_phase333_multitopic_host_contract,
)
from .canonical_source_phase34_structured_output_contract import (
    install as _install_canonical_source_phase34_structured_output_contract,
)
from .canonical_source_phase341_schema_completeness_contract import (
    install as _install_canonical_source_phase341_schema_completeness_contract,
)
from .canonical_source_phase342_pdf_semantic_salvage_contract import (
    install as _install_canonical_source_phase342_pdf_semantic_salvage_contract,
)
from .canonical_source_phase35_provider_max_contract import (
    install as _install_canonical_source_phase35_provider_max_contract,
)
from .canonical_source_phase351_workbook_capacity_contract import (
    install as _install_canonical_source_phase351_workbook_capacity_contract,
)
from .canonical_source_phase352_chunk_override_compat import (
    install as _install_canonical_source_phase352_chunk_override_compat,
)
from .canonical_source_phase36_source_critical_turnover_contract import (
    install as _install_canonical_source_phase36_source_critical_turnover_contract,
)
from .canonical_source_phase37_visual_topology_convergence_contract import (
    install as _install_canonical_source_phase37_visual_topology_convergence_contract,
)
from .canonical_source_phase371_visual_topology_compat import (
    install as _install_canonical_source_phase371_visual_topology_compat,
)
from .canonical_source_phase38_boundary_grounding_turnover_contract import (
    install as _install_canonical_source_phase38_boundary_grounding_turnover_contract,
)
from .canonical_source_phase39_post_freeze_hub_convergence_contract import (
    install as _install_canonical_source_phase39_post_freeze_hub_convergence_contract,
)
from .canonical_source_phase310_terminal_figure_inventory_convergence_contract import (
    install as _install_canonical_source_phase310_terminal_figure_inventory_convergence_contract,
)
from .canonical_source_phase311_acsd_visual_display_projection_contract import (
    install as _install_canonical_source_phase311_acsd_visual_display_projection_contract,
)
from .chapter_reading_contract import (
    install as _install_chapter_reading_contract,
)
from .question_polishing_contract import (
    install as _install_question_polishing_contract,
)

# Production order is intentional and fail-closed: preserve source identity first,
# defer Type allocation to the topology freeze, restore integration boundaries,
# normalize source-owned display, and reconcile explicit Figure tags before every
# strict terminal gate. Phase 1 compiles the audit bundle; Phase 2 consumes its
# deterministic task ledger; Phase 2.1 hardens source structure and ownership;
# Phase 2.1.1 normalizes task-list display and improves diagnostics; Phase 2.2
# then adjudicates only unresolved original-document evidence before generation.
# Phase 2.2.1 hardens evidence addressing and owns the verified GPT PDF-to-ACSD
# reader that every PDF conversion goes through. Phase 2.2.2
# classifies repeated running navigation without polluting semantic MMD. Phase 3
# unifies every source channel into stable semantic IDs. Phase 3.1 narrows and
# caches exact concept-to-block grounding. Phase 3.2 source-verifies, moves,
# refines, or splits concepts before learner analysis. Phase 3.3 installs last to
# make that adjudication resumable, feed exact-grounding rejection back into one
# bounded topology retry, and certify every normal Type/Case to an existing or
# necessary new source-grounded concept before final topology freeze. Phase 3.3.1
# makes that independently verified host identity authoritative throughout the
# legacy assignment and semantic host-review passes. Phase 3.3.2 invalidates
# pre-Phase3.3 whole-topology caches once and bypasses them during grounding-led
# reconsideration without discarding the new per-concept verified decisions.
# Phase 3.3.3 namespaces locally generated host keys by topic so more than one
# canonical topic can safely add a necessary concept in the same chapter. Phase
# 3.4 turns completion-limit failures into adaptive strict-schema retries and
# resumable hierarchy batches. Phase 3.4.1 rejects syntactically valid but
# schema-incomplete objects before they can masquerade as completed output. Phase
# 3.4.2 isolates unresolved PDF batches and permits one final evidence-bound GPT
# semantic reconstruction before the unchanged source verifier can stop the run.
# Phase 3.5 treats model limits as ceilings while retaining purpose-specific
# completion budgets and bounded semantic evidence views; the durable canonical
# source remains available for targeted expansion, and oversized inputs are
# losslessly batched. Phase 3.5.1 applies its workbook capacity policy to the
# vendored revision-workbook planner and authoring passes. Phase 3.5.2 retains an
# explicit caller/test chunk boundary without changing the production default.
# Phase 3.6 installs after every source and
# token contract so any remaining PDF source-critical failure is automatically
# promoted to the full verified GPT PDF-to-ACSD lane during conversion or Resume.
# Phase 3.7 installs last so visual captions/original-page evidence survive every
# topology/grounding/Type-host packet. Automatic retirement/deletion is disabled:
# a row must be kept, moved, refined, transactionally split, or fail closed.
# Phase 3.7.1 preserves exact plain-text compatibility and clears provisional
# audit state between topology convergence passes. Phase 3.8 installs after every earlier topology
# wrapper so exact grounding can inspect bounded adjacent-topic continuation
# evidence and feed only the rejected original concept back through convergence.
# Phase 3.9 installs last so source-owned Activity/Info Hub notes and their private
# qid markers are rebuilt after final Type taxonomy rendering and compared using
# the exact idempotent wire text shipped by the Phase 2.1 normalizer. Phase 3.10
# installs outermost so terminal Figure repair and exact inventory coverage use
# one source-registry projection before post-freeze, final, checkpoint, or deposit
# validation; one deterministic retry handles presentation drift from old wrappers.
# Phase 3.11 installs after Phase 3.10 so a verified registry projection overrides
# the immutable Phase 2 ACSD display only for public visual tags. Raw ACSD wording,
# qids, Figure identities, and source inventory remain untouched.
_install_closed_inventory_contract(generation)
_install_concept_topology_contract(generation)
_install_concept_topology_compat(generation)
_install_source_visual_contract(generation)
_install_terminal_figure_contract(generation)
_install_canonical_source_contract()
_install_canonical_source_phase2_contract()
_install_canonical_source_phase2_compat(generation)
_install_canonical_source_phase21_contract(generation)
_install_canonical_source_phase211_contract(generation)
_install_canonical_source_phase22_contract(generation)
_install_canonical_source_phase221_contract()
_install_canonical_source_phase222_contract()
_install_canonical_source_phase3_contract(generation)
_install_canonical_source_phase31_grounding_contract(generation)
_install_canonical_source_phase32_topology_adjudication_contract(generation)
_install_canonical_source_phase33_preflight_contract(generation)
_install_canonical_source_phase331_host_authority_contract(generation)
_install_canonical_source_phase332_cache_compat_contract(generation)
_install_canonical_source_phase333_multitopic_host_contract(generation)
_install_canonical_source_phase34_structured_output_contract()
_install_canonical_source_phase341_schema_completeness_contract()
_install_canonical_source_phase342_pdf_semantic_salvage_contract()
_install_canonical_source_phase35_provider_max_contract()
_install_canonical_source_phase351_workbook_capacity_contract()
_install_canonical_source_phase352_chunk_override_compat()
_install_canonical_source_phase36_source_critical_turnover_contract()
_install_canonical_source_phase37_visual_topology_convergence_contract()
_install_canonical_source_phase371_visual_topology_compat()
_install_canonical_source_phase38_boundary_grounding_turnover_contract()
_install_canonical_source_phase39_post_freeze_hub_convergence_contract(generation)
_install_canonical_source_phase310_terminal_figure_inventory_convergence_contract(generation)
_install_canonical_source_phase311_acsd_visual_display_projection_contract(generation)
# Pass 1 (docs/build-concepts-manual-process.md) installs outermost: a live
# run reads and normalizes its source before any wrapper below compiles it,
# and the deposit/recovery semantic source resolves through the same reading.
_install_chapter_reading_contract(generation)
# Pass 4 (docs/build-concepts-manual-process.md) polishes the freshly
# extracted question inventory before it is checkpointed, and presents the
# polished wording wherever public Example text is rendered.
_install_question_polishing_contract(generation)

del _install_closed_inventory_contract
del _install_concept_topology_contract
del _install_concept_topology_compat
del _install_source_visual_contract
del _install_terminal_figure_contract
del _install_canonical_source_contract
del _install_canonical_source_phase2_contract
del _install_canonical_source_phase2_compat
del _install_canonical_source_phase21_contract
del _install_canonical_source_phase211_contract
del _install_canonical_source_phase22_contract
del _install_canonical_source_phase221_contract
del _install_canonical_source_phase222_contract
del _install_canonical_source_phase3_contract
del _install_canonical_source_phase31_grounding_contract
del _install_canonical_source_phase32_topology_adjudication_contract
del _install_canonical_source_phase33_preflight_contract
del _install_canonical_source_phase331_host_authority_contract
del _install_canonical_source_phase332_cache_compat_contract
del _install_canonical_source_phase333_multitopic_host_contract
del _install_canonical_source_phase34_structured_output_contract
del _install_canonical_source_phase341_schema_completeness_contract
del _install_canonical_source_phase342_pdf_semantic_salvage_contract
del _install_canonical_source_phase35_provider_max_contract
del _install_canonical_source_phase351_workbook_capacity_contract
del _install_canonical_source_phase352_chunk_override_compat
del _install_canonical_source_phase36_source_critical_turnover_contract
del _install_canonical_source_phase37_visual_topology_convergence_contract
del _install_canonical_source_phase371_visual_topology_compat
del _install_canonical_source_phase38_boundary_grounding_turnover_contract
del _install_canonical_source_phase39_post_freeze_hub_convergence_contract
del _install_canonical_source_phase310_terminal_figure_inventory_convergence_contract
del _install_canonical_source_phase311_acsd_visual_display_projection_contract
del _install_chapter_reading_contract
del _install_question_polishing_contract
