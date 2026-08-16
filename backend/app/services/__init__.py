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
# unifies every source channel into stable semantic IDs.
#
# Everything after the 81% (pre_type_assignment) checkpoint runs through the
# rewritten Phase 3 package (app/services/phase3: envelope, kernel, Settle,
# Host, Polish, Assemble) via the concept-topology contract's prepare seam.
# The legacy 3.1-3.11 post-81% contract stack is deleted
# (docs/phase3-rewrite-spec.md §9, PR 4).
#
# The remaining numbered contracts below are pre-81% / cross-product
# transport, despite their phase-3.x names: Phase 3.4 turns
# completion-limit failures into adaptive strict-schema retries and resumable
# hierarchy batches for the pre-81% semantic-graph classifier. Phase 3.4.1
# rejects syntactically valid but schema-incomplete objects before they can
# masquerade as completed output. Phase 3.4.2 isolates unresolved PDF batches
# and permits one final evidence-bound GPT semantic reconstruction before the
# unchanged source verifier can stop the run. Phase 3.5 treats model limits as
# ceilings while retaining purpose-specific completion budgets and bounded
# semantic evidence views; the durable canonical source remains available for
# targeted expansion, and oversized inputs are losslessly batched. Phase 3.5.1
# applies its workbook capacity policy to the vendored revision-workbook
# planner and authoring passes. Phase 3.5.2 retains an explicit caller/test
# chunk boundary without changing the production default. Phase 3.6 installs
# after every source and token contract so any remaining PDF source-critical
# failure is automatically promoted to the full verified GPT PDF-to-ACSD lane
# during conversion or Resume.
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
_install_canonical_source_phase34_structured_output_contract()
_install_canonical_source_phase341_schema_completeness_contract()
_install_canonical_source_phase342_pdf_semantic_salvage_contract()
_install_canonical_source_phase35_provider_max_contract()
_install_canonical_source_phase351_workbook_capacity_contract()
_install_canonical_source_phase352_chunk_override_compat()
_install_canonical_source_phase36_source_critical_turnover_contract()
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
del _install_canonical_source_phase34_structured_output_contract
del _install_canonical_source_phase341_schema_completeness_contract
del _install_canonical_source_phase342_pdf_semantic_salvage_contract
del _install_canonical_source_phase35_provider_max_contract
del _install_canonical_source_phase351_workbook_capacity_contract
del _install_canonical_source_phase352_chunk_override_compat
del _install_canonical_source_phase36_source_critical_turnover_contract
del _install_chapter_reading_contract
del _install_question_polishing_contract
