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
from .canonical_source_phase3_contract import (
    install as _install_canonical_source_phase3_contract,
)
from .canonical_source_phase3_topology_contract import (
    install as _install_canonical_source_phase3_topology_contract,
)

# Production order is intentional and fail-closed: preserve source identity first,
# defer Type allocation to the topology freeze, restore integration boundaries,
# normalize source-owned display, and reconcile explicit Figure tags before every
# strict terminal gate. Phase 1 compiles the audit bundle; Phase 2 consumes its
# deterministic task ledger; Phase 2.1 hardens source structure and ownership;
# Phase 2.1.1 normalizes task-list display and improves diagnostics; Phase 2.2
# then adjudicates only unresolved original-document evidence before generation.
# Phase 2.2.1 hardens evidence addressing and adds a verified GPT PDF-to-ACSD
# fallback only for hard or objectively unusable Mathpix conversions.
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
_install_canonical_source_phase3_contract(generation)
_install_canonical_source_phase3_topology_contract(generation)

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
del _install_canonical_source_phase3_contract
del _install_canonical_source_phase3_topology_contract
