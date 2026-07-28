"""Shared service-level contract registration."""

from . import generation as generation
from .closed_inventory_contract import install as _install_closed_inventory_contract
from .concept_topology_contract import install as _install_concept_topology_contract
from .concept_topology_compat import install as _install_concept_topology_compat
from .source_visual_contract import install as _install_source_visual_contract
from .terminal_figure_contract import install as _install_terminal_figure_contract
from .canonical_source_contract import install as _install_canonical_source_contract

# Production order is intentional and fail-closed: preserve source identity first,
# defer Type allocation to the topology freeze, restore integration boundaries,
# normalize source-owned display, and reconcile explicit Figure tags before every
# strict terminal gate. The canonical-source compiler is installed last because it
# is an additive conversion shadow and never replaces the raw-MMD generation path.
_install_closed_inventory_contract(generation)
_install_concept_topology_contract(generation)
_install_concept_topology_compat(generation)
_install_source_visual_contract(generation)
_install_terminal_figure_contract(generation)
_install_canonical_source_contract()

del _install_closed_inventory_contract
del _install_concept_topology_contract
del _install_concept_topology_compat
del _install_source_visual_contract
del _install_terminal_figure_contract
del _install_canonical_source_contract
