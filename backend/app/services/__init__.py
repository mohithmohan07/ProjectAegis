"""Shared service-level contract registration."""

from . import generation as generation
from .closed_inventory_contract import install as _install_closed_inventory_contract

_install_closed_inventory_contract(generation)

del _install_closed_inventory_contract
