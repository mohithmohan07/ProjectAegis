"""Install graph-authoritative concept topology and Type allocation."""
from __future__ import annotations

import copy
from functools import wraps
from types import ModuleType

from . import canonical_source_phase3 as phase3
from . import canonical_source_phase3_topology as topology

_CONTRACT_VERSION = 1


def install(generation: ModuleType) -> None:
    if getattr(generation, "_PHASE3_TOPOLOGY_CONTRACT_VERSION", 0) >= _CONTRACT_VERSION:
        return

    original_topic_headings = generation._topic_headings
    original_group_topic_excerpts = generation._group_source_topic_excerpts
    original_snap_topics = generation._snap_topics_to_headings
    original_recover_missing_topics = generation._recover_missing_topic_concepts_via_api
    original_build_culminations = generation._build_culminations_via_api
    original_expand_assignment_units = generation._expand_mined_types_to_assignment_units
    original_assign_mined_types = generation._assign_mined_types_via_api

    @wraps(original_topic_headings)
    def topic_headings(sections):
        graph = phase3.active_graph()
        if graph:
            titles = topology.graph_topic_titles(graph)
            if titles:
                return titles
        return original_topic_headings(sections)

    @wraps(original_group_topic_excerpts)
    def group_topic_excerpts(sections):
        graph = phase3.active_graph()
        if graph:
            excerpts = topology.source_topic_excerpts(graph)
            if excerpts:
                return excerpts
        return original_group_topic_excerpts(sections)

    @wraps(original_snap_topics)
    def snap_topics(records, headings, *args, **kwargs):
        graph = phase3.active_graph()
        prepared = (
            topology.annotate_concept_topics(records, graph)
            if graph else records
        )
        snapped = original_snap_topics(prepared, headings, *args, **kwargs)
        return (
            topology.annotate_concept_topics(snapped, graph)
            if graph else snapped
        )

    @wraps(original_recover_missing_topics)
    def recover_missing_topics(records, *args, **kwargs):
        recovered = original_recover_missing_topics(records, *args, **kwargs)
        graph = phase3.active_graph()
        return (
            topology.annotate_concept_topics(recovered, graph)
            if graph else recovered
        )

    @wraps(original_build_culminations)
    def build_culminations(records, *args, **kwargs):
        culminations = original_build_culminations(records, *args, **kwargs)
        graph = phase3.active_graph()
        return (
            topology.annotate_concept_topics(culminations, graph)
            if graph else culminations
        )

    @wraps(original_expand_assignment_units)
    def expand_assignment_units(types):
        units = original_expand_assignment_units(types)
        graph = phase3.active_graph()
        return (
            topology.canonicalize_assignment_units(units, graph)
            if graph else units
        )

    @wraps(original_assign_mined_types)
    def assign_mined_types(records, *, meta, mined_types, **kwargs):
        graph = phase3.active_graph()
        if not graph:
            return original_assign_mined_types(
                records, meta=meta, mined_types=mined_types, **kwargs
            )
        prepared_records = topology.annotate_concept_topics(records, graph)
        canonical_mined = topology.canonicalize_mined_type_topics(
            mined_types, graph
        )
        if isinstance(mined_types, dict):
            mined_types.clear()
            mined_types.update(copy.deepcopy(canonical_mined))
            canonical_mined = mined_types
        result = original_assign_mined_types(
            prepared_records,
            meta=meta,
            mined_types=canonical_mined,
            **kwargs,
        )
        return topology.annotate_concept_topics(result, graph)

    generation._topic_headings = topic_headings
    generation._group_source_topic_excerpts = group_topic_excerpts
    generation._snap_topics_to_headings = snap_topics
    generation._recover_missing_topic_concepts_via_api = recover_missing_topics
    generation._build_culminations_via_api = build_culminations
    generation._expand_mined_types_to_assignment_units = expand_assignment_units
    generation._assign_mined_types_via_api = assign_mined_types
    generation._PHASE3_TOPOLOGY_CONTRACT_VERSION = _CONTRACT_VERSION
