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

# Production order is intentional and fail-closed: preserve source identity first,
# defer Type allocation to the topology freeze, restore integration boundaries,
# normalize source-owned display, and reconcile explicit Figure tags before every
# strict terminal gate. Phase 1 then compiles the canonical source artifacts; Phase
# 2 consumes that verified task ledger while semantic concept extraction continues
# to use the immutable raw MMD. Compatibility is installed last so old checkpoints
# retain the deepest safe semantic recovery point during the source-contract cutover.
_install_closed_inventory_contract(generation)
_install_concept_topology_contract(generation)
_install_concept_topology_compat(generation)
_install_source_visual_contract(generation)
_install_terminal_figure_contract(generation)
_install_canonical_source_contract()
_install_canonical_source_phase2_contract()
_install_canonical_source_phase2_compat(generation)

del _install_closed_inventory_contract
del _install_concept_topology_contract
del _install_concept_topology_compat
del _install_source_visual_contract
del _install_terminal_figure_contract
del _install_canonical_source_contract
del _install_canonical_source_phase2_contract
del _install_canonical_source_phase2_compat
