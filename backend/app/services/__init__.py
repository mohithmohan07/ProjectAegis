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

# Production order is intentional and fail-closed: preserve source identity first,
# defer Type allocation to the topology freeze, restore integration boundaries,
# normalize source-owned display, and reconcile explicit Figure tags before every
# strict terminal gate. Phase 1 compiles the audit bundle; Phase 2 consumes its
# deterministic task ledger; Phase 2.1 hardens source structure and ownership;
# Phase 2.1.1 normalizes task-list display and improves diagnostics; Phase 2.2
# then adjudicates only unresolved original-document evidence before generation.
# Phase 2.2.1 hardens evidence addressing and adds a verified GPT PDF-to-ACSD
# fallback only for hard or objectively unusable Mathpix conversions. Phase 2.2.2
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
# Phase 3.5 installs last and removes Aegis-local token ceilings below the model's
# provider limits: maximum output is requested on every live call, complete source
# evidence is retained up to the context boundary, and oversized inputs are
# losslessly batched rather than trimmed. Phase 3.5.1 applies the same capacity
# policy to the vendored revision-workbook planner and authoring passes.
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
