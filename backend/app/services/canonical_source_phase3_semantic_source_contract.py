"""Install the graph-derived semantic source at the concept parsing boundary."""
from __future__ import annotations

from functools import wraps
from types import ModuleType

from . import canonical_source_phase3 as phase3
from . import canonical_source_phase3_contract as graph_contract
from . import canonical_source_phase3_semantic_source as semantic_source
from . import progress, uploads

_CONTRACT_VERSION = 1


def install(generation: ModuleType) -> None:
    if getattr(generation, "_PHASE3_SEMANTIC_SOURCE_VERSION", 0) >= _CONTRACT_VERSION:
        return

    graph_contract.OPTIONAL_ARTIFACT_SPECS.update(
        semantic_source.ARTIFACT_SPECS
    )
    original_prepare_job_graph = graph_contract.prepare_job_graph
    original_section_aware_chunks = generation._section_aware_chunks

    @wraps(original_prepare_job_graph)
    def prepare_job_graph(job, *, metadata=None):
        graph = original_prepare_job_graph(job, metadata=metadata)
        report = semantic_source.write_artifacts(
            uploads.source_artifact_directory(int(job.id)), graph
        )
        progress.log(
            "Phase 3 graph-derived semantic MMD ready: "
            f"{report['summary']['topics']} topic(s), "
            f"{report['summary']['images']} image(s), "
            f"{report['summary']['katex_spans']} KaTeX span(s); "
            "raw publisher layout commands are excluded from semantic concept calls.",
            level="success",
        )
        return graph

    @wraps(original_section_aware_chunks)
    def section_aware_chunks(mmd_text, *args, **kwargs):
        graph = phase3.active_graph()
        if graph:
            mmd_text = semantic_source.render_semantic_source(graph)
        return original_section_aware_chunks(mmd_text, *args, **kwargs)

    graph_contract.prepare_job_graph = prepare_job_graph
    generation._section_aware_chunks = section_aware_chunks
    generation._PHASE3_SEMANTIC_SOURCE_VERSION = _CONTRACT_VERSION
