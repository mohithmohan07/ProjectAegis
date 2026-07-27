"""Shared service-level contract registration."""

from . import generation as generation
from .closed_inventory_contract import install as _install_closed_inventory_contract
from .concept_topology_contract import install as _install_concept_topology_contract
from .concept_topology_compat import install as _install_concept_topology_compat

# Production order is intentional and fail-closed: preserve source identity first,
# defer Type allocation to the topology freeze, then restore integration boundaries.
_install_closed_inventory_contract(generation)
_install_concept_topology_contract(generation)
_install_concept_topology_compat(generation)

del _install_closed_inventory_contract
del _install_concept_topology_contract
del _install_concept_topology_compat
