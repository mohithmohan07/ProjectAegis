"""Mechanical projection of grounded PDF IR into the existing generation contract.

The IR owns identities and relationships. Markdown is a disposable view, never
input to a parser. A rendering defect substitutes the exact source region; it
does not rewrite the transcription or invalidate readable source evidence.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
from typing import Any, Callable

from . import katex_rules as kr
from .pdf_source_models import DocumentStructure, SCHEMA_VERSION, validate_structure

ADAPTER_VERSION = "pdf-source-canonical-1"
ENGINE_VERSION = "pdf-source-engine-1"
CONTRACT_MODE = "acsd-phase2-source-critical"


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_canonical(value: Any) -> bool:
    return (isinstance(value, dict)
            and value.get("pdf_source_engine") == ENGINE_VERSION
            and value.get("compiler_version") == ADAPTER_VERSION)


def literal_text(value: str) -> str:
    """Escape view syntax, without interpreting or normalizing printed text."""
    value = html.escape(str(value), quote=False)
    # Entity encoding prevents rich-text readers from treating printed tag
    # examples as instructions while keeping their visible characters intact.
    return "".join(f"&#{ord(character)};" if character in "\\[]`*_{}#+!|$" else character
                   for character in value)


def block_evidence_text(block: dict[str, Any]) -> str:
    """Model evidence is distinct from the learner-facing rich-text view."""
    display = str(block.get("display_text") or "")
    source = block.get("source_ir") or {}
    if block.get("rendered_as_source_crop"):
        # Coordinates and cell spans stay structured. Do not guess an inline
        # placement for mixed math or flatten spanning cells into a fake table.
        transcription = {key: copy.deepcopy(source.get(key)) for key in
                         ("block_id", "page_id", "page_number", "bbox", "kind", "content", "latex",
                          "table_cells", "printed_label", "figure_caption", "legibility", "uncertainty_notes")}
        return display + "\n[Grounded source transcription] " + json.dumps(transcription, ensure_ascii=False)
    caption = str(source.get("figure_caption") or "")
    return display + "\n[Source caption] " + literal_text(caption) if caption else display


def project_document(
    document: dict[str, Any], *, source_filename: str, job_id: int,
    evidence: dict[str, Any], cropper: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from .pdf_source_evidence import crop_region

    cropper = cropper or crop_region
    if (document.get("schema_version") != SCHEMA_VERSION
            or document.get("engine_version") != ENGINE_VERSION
            or document.get("status") not in {"verified", "review_required"}
            or document.get("pdf_sha256") != evidence.get("pdf_sha256")):
        raise ValueError("PDF source IR does not match its evidence contract")
    source_rows = [block for page in document["pages"] for block in page["blocks"]]
    ids = [row["block_id"] for row in source_rows]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("PDF source block identities must be unique")
    structure = DocumentStructure.model_validate(document["structure"])
    validate_structure(structure, set(ids))
    section_by_block = {
        block_id: section.section_id for section in structure.sections
        for block_id in section.block_ids
    }
    sections_by_id = {row.section_id: row for row in structure.sections}
    blocks: list[dict[str, Any]] = []
    figures: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    render_fallbacks: list[dict[str, Any]] = []
    pieces: list[str] = []
    position = 0
    for order, raw in enumerate(source_rows, 1):
        source = copy.deepcopy(raw)
        block_id = source["block_id"]
        latex = str(source.get("latex") or "")
        content = str(source.get("content") or "")
        kind = str(source["kind"])
        display = kr.katex(latex) if kind == "equation" and latex else literal_text(content)
        defects = kr.rich_text_issues(display)
        needs_crop = kind in {"figure", "table"} or bool(defects) or bool(latex and kind != "equation")
        # Empty or partly readable regions still have visible evidence. Do not
        # invent a transcription, and do not silently remove the region.
        needs_crop = needs_crop or not display.strip() or source.get("legibility") != "legible"
        figure_id = ""
        image_ids: list[str] = []
        if needs_crop:
            crop = cropper(evidence, int(source["page_number"]), source["bbox"], job_id=job_id)
            figure_id, image_id = f"figure:{block_id}", f"image:{block_id}"
            caption = str(source.get("figure_caption") or "")
            display = kr.image(crop["url"], caption or f"Source page {source['page_number']} region")
            image_ids = [image_id]
            images.append({"image_id": image_id, "url": crop["url"], "alt_raw": caption,
                           "source_block_id": block_id, "source_region": copy.deepcopy(crop)})
            figures.append({"figure_id": figure_id, "image_ids": image_ids,
                            "caption_raw": caption, "caption_display": literal_text(caption),
                            "source_block_id": block_id, "source_region": copy.deepcopy(crop)})
            if kind not in {"figure", "table"}:
                render_fallbacks.append({"block_id": block_id, "reason": defects or ["source_region_view"],
                                         "source_region": copy.deepcopy(crop)})
        section_id = section_by_block.get(block_id, "")
        section = sections_by_id.get(section_id)
        view = display
        if kind == "heading" and not needs_crop:
            view = "#" * min(6, section.level if section else 1) + " " + display
        start, end = position, position + len(view)
        block = {
            "block_id": block_id, "order": order, "kind": kind,
            "section_id": section_id, "source_start": start, "source_end": end,
            "raw_text": view, "display_text": display, "raw_sha256": text_sha256(view),
            "source_content": content, "source_ir": source,
            "page_id": source["page_id"], "page_number": source["page_number"],
            "bbox": copy.deepcopy(source["bbox"]), "task_ids": [],
            "figure_id": figure_id, "image_ids": image_ids,
            "heading": {"title": literal_text(content), "level": section.level if section else 1,
                        "number": ""} if kind == "heading" and not needs_crop else {},
            "rendered_as_source_crop": needs_crop,
        }
        # Cropped headings must survive graph rendering; the image is their
        # actual view and must not be replaced with the malformed title.
        if kind == "heading" and needs_crop:
            block["kind"] = "paragraph"
        block["source_evidence_text"] = block_evidence_text(block)
        blocks.append(block)
        pieces.append(view)
        position = end + 2
        for figure in figures[-1:] if figure_id else []:
            figure.update(source_start=start, source_end=end)
    mmd_text = "\n\n".join(pieces)
    by_id = {row["block_id"]: row for row in blocks}

    def ordered(values):
        return sorted(set(values), key=lambda key: by_id[key]["order"])

    def joined(values, field="display_text"):
        return "\n\n".join(str(by_id[key][field]) for key in ordered(values))

    sections = []
    for order, section in enumerate(structure.sections, 1):
        refs = ordered(section.block_ids)
        sections.append({**section.model_dump(), "order": order,
                         "title": literal_text(section.title), "source_title": section.title,
                         "source_start": min((by_id[key]["source_start"] for key in refs), default=0),
                         "source_end": max((by_id[key]["source_end"] for key in refs), default=0)})
    roots = [task for task in structure.tasks if not task.parent_task_id]
    task_by_id = {task.task_id: task for task in structure.tasks}

    def root_id(task):
        while task.parent_task_id:
            task = task_by_id[task.parent_task_id]
        return task.task_id

    members = {task.task_id: [] for task in roots}
    for task in structure.tasks:
        members[root_id(task)].append(task)
    roots.sort(key=lambda task: min(by_id[key]["order"] for member in members[task.task_id]
                                   for key in member.prompt_block_ids))
    tasks = []
    for order, task in enumerate(roots, 1):
        group = members[task.task_id]
        prompts = ordered(key for member in group for key in member.prompt_block_ids)
        contexts = ordered(key for member in group for key in member.context_block_ids)
        answers = ordered(key for member in group for key in member.answer_block_ids)
        visuals = ordered(key for member in group for key in member.figure_block_ids)
        refs = ordered(prompts + contexts + answers + visuals)
        prompt_refs = ordered(prompts + visuals)
        figure_refs = [by_id[key]["figure_id"] for key in ordered(prompts + contexts + visuals)
                       if by_id[key]["figure_id"]]
        urls = [image["url"] for image in images if f"figure:{image['source_block_id']}" in figure_refs]
        row = {
            "task_id": task.task_id, "qid": f"QINV-{order:04d}", "order": order,
            "identity_key": f"{document['pdf_sha256']}:{task.task_id}",
            "section_id": task.section_id, "source_kind": task.kind, "source_label": task.label,
            "topic_hint": literal_text(sections_by_id[task.section_id].title) if task.section_id else "",
            "source_start": min(by_id[key]["source_start"] for key in prompts),
            "source_end": max(by_id[key]["source_end"] for key in prompts),
            "source_block_ids": refs, "prompt_block_ids": prompts,
            "qx_context_block_ids": contexts, "answer_block_ids": answers,
            "display_prompt": joined(prompt_refs), "raw_prompt": joined(prompts, "source_content") or joined(prompts),
            "raw_solution_or_answer": joined(answers, "source_content"),
            "shared_context": joined(contexts), "requires_context": bool(contexts),
            "requires_visual": bool(urls), "figure_refs": figure_refs, "image_urls": urls,
            "page_hint": ", ".join(str(value) for value in sorted({by_id[key]["page_number"] for key in refs})),
            "membership_authority": "model_verdict", "origin": ENGINE_VERSION,
            "source_task_ids": [member.task_id for member in group],
            "source_task_tree": [member.model_dump() for member in group], "leaf_cases": [],
            "content_objects": {"pdf_source": {
                role: [copy.deepcopy(by_id[key]["source_ir"]) for key in role_refs]
                for role, role_refs in (("prompt", prompts), ("context", contexts), ("answer", answers), ("figure", visuals))
            }},
        }
        tasks.append(row)
        for key in refs:
            by_id[key]["task_ids"].append(task.task_id)
    view_hash = text_sha256(mmd_text)
    from .pdf_source_evidence import digest_json
    canonical = {
        "schema_name": "Aegis Canonical Source Document", "schema_version": "1.1.0",
        "compiler_version": ADAPTER_VERSION, "pdf_source_engine": ENGINE_VERSION,
        "phase": "pdf-source", "consumer_module": "build_concepts", "shadow_mode": False,
        "used_for_generation": True, "phase2_inventory_ready": True,
        "document": {"source_filename": source_filename, "chapter_title": literal_text(structure.title),
                     "source_sha256": view_hash, "source_chars": len(mmd_text),
                     "pdf_sha256": document["pdf_sha256"],
                     "source_ir_sha256": document.get("document_sha256") or digest_json(document)},
        "source_contract": {"mode": CONTRACT_MODE, "schema_version": "1.1.0",
                            "compiler_version": ADAPTER_VERSION, "source_reader": ENGINE_VERSION,
                            "source_sha256": view_hash, "pdf_sha256": document["pdf_sha256"],
                            "section_sequence": [row["section_id"] for row in sections],
                            "task_count": len(tasks), "consumer_module": "build_concepts"},
        "sections": sections, "blocks": blocks, "tasks": tasks, "figures": figures,
        "images": images, "math_spans": [], "math": [], "source_relations": copy.deepcopy(document["structure"]["relations"]),
        "source_structure": copy.deepcopy(document["structure"]),
        "source_review": {"status": document["status"], "audit": copy.deepcopy(document.get("audit") or {}),
                          "page_audits": [{"page_id": page["page_id"], "audit": copy.deepcopy(page["audit"])}
                                          for page in document["pages"]]},
        "render_fallbacks": render_fallbacks,
        "shadow_validation": {"phase2_inventory_ready": True, "phase2_blocking_issues": []},
    }
    report = {"schema_version": canonical["schema_version"], "compiler_version": ADAPTER_VERSION,
              "status": document["status"], "source_sha256": view_hash, "pdf_sha256": document["pdf_sha256"],
              "used_for_generation": True, "phase2_inventory_ready": True, "issues": [],
              "source_review": copy.deepcopy(canonical["source_review"]), "render_fallbacks": render_fallbacks,
              "summary": {"sections": len(sections), "blocks": len(blocks), "tasks": len(tasks),
                          "figures": len(figures), "images": len(images), "source_chars": len(mmd_text),
                          "errors": 0, "warnings": len(render_fallbacks)}}
    return {"mmd_text": mmd_text, "canonical": canonical, "report": report}


def page_evidence(document: dict[str, Any], canonical: dict[str, Any]) -> dict[str, Any]:
    """Adapt field names only; retain the model's exact page/block identities."""
    by_id = {block["block_id"]: block for block in canonical["blocks"]}
    images = {image["source_block_id"]: image for image in canonical["images"]}
    pages = copy.deepcopy(document["pages"])
    for page in pages:
        for order, block in enumerate(page["blocks"], 1):
            projected = by_id[block["block_id"]]
            block.update(reading_order=order, text=block["content"],
                         caption=block.get("figure_caption") or "", heading_level=projected["heading"].get("level", 1),
                         confidence=1.0 if page["audit"].get("verdict") == "verified" else 0.0,
                         display_text=projected["display_text"],
                         asset_url=images.get(block["block_id"], {}).get("url", ""))
    return {"schema_version": SCHEMA_VERSION, "compiler_version": ENGINE_VERSION,
            "ingestion_contract_version": ENGINE_VERSION, "pdf_sha256": document["pdf_sha256"],
            "status": document["status"], "pages": pages, "page_count": len(pages),
            "audit": copy.deepcopy(document.get("audit") or {})}


def render_semantic_source(graph: dict[str, Any], canonical: dict[str, Any]) -> str:
    """Render grounded views without legacy text cleaning or source rewriting."""
    sections = {row["section_id"]: row for row in graph.get("sections", [])}
    pieces = []
    for block in sorted(canonical["blocks"], key=lambda row: row["order"]):
        display = block["display_text"]
        if block["kind"] == "heading":
            role = sections.get(block["section_id"], {}).get("role", "content_heading")
            level = {"chapter_heading": 1, "main_topic": 2, "subtopic": 3}.get(role, 3)
            display = "#" * level + " " + display
        pieces.append(display)
    return "\n\n".join(pieces)
