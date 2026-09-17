"""Fresh PDF source IR: model-owned meaning, mechanically checked references.

These models are independent of Markdown and of the historical source reader.
All response fields are required so the same schema closes the provider wire
contract and the local validator. Empty strings/arrays explicitly mean absent.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "pdf-source-ir-1"


class SourceModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class TableCell(SourceModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    content: str
    latex: str


class PageBlock(SourceModel):
    bbox: list[float] = Field(min_length=4, max_length=4)
    kind: Literal[
        "heading", "paragraph", "list", "table", "figure", "equation",
        "task", "answer", "header", "footer", "other",
    ]
    content: str
    latex: str
    table_cells: list[TableCell]
    printed_label: str
    figure_caption: str
    legibility: Literal["legible", "partly_legible", "unreadable"]
    uncertainty_notes: list[str]

    @model_validator(mode="after")
    def valid_geometry(self):
        x0, y0, x1, y1 = self.bbox
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("block bbox must be a positive normalized rectangle")
        # Compare declared rectangles without allocating a model-sized grid.
        for index, cell in enumerate(self.table_cells):
            for earlier in self.table_cells[:index]:
                if (cell.row < earlier.row + earlier.row_span
                        and earlier.row < cell.row + cell.row_span
                        and cell.column < earlier.column + earlier.column_span
                        and earlier.column < cell.column + cell.column_span):
                    raise ValueError("table cells overlap")
        return self


class PageDraft(SourceModel):
    page_number: int = Field(ge=1)
    source_usable: bool
    blocks: list[PageBlock]
    review_notes: list[str]


class AuditIssue(SourceModel):
    code: str
    message: str
    block_ids: list[str]
    bbox: list[float]

    @model_validator(mode="after")
    def valid_geometry(self):
        if self.bbox:
            if len(self.bbox) != 4:
                raise ValueError("issue bbox must be empty or four coordinates")
            x0, y0, x1, y1 = self.bbox
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("issue bbox must be normalized")
        return self


class SourceAudit(SourceModel):
    verdict: Literal["verified", "needs_repair", "unreadable"]
    source_usable: bool
    issues: list[AuditIssue]

    @model_validator(mode="after")
    def coherent_verdict(self):
        if self.verdict == "verified" and (not self.source_usable or self.issues):
            raise ValueError("verified audit cannot contain unresolved issues")
        if self.verdict == "unreadable" and self.source_usable:
            raise ValueError("unreadable audit cannot mark source usable")
        return self


class SourceSection(SourceModel):
    section_id: str
    title: str
    parent_section_id: str
    level: int = Field(ge=1)
    block_ids: list[str]


class SourceTask(SourceModel):
    task_id: str
    label: str
    kind: str
    parent_task_id: str
    section_id: str
    prompt_block_ids: list[str]
    context_block_ids: list[str]
    answer_block_ids: list[str]
    figure_block_ids: list[str]


class BlockRelation(SourceModel):
    kind: Literal["continues", "context_for", "answer_for", "caption_for", "refers_to"]
    from_block_id: str
    to_block_id: str


class DocumentStructure(SourceModel):
    title: str
    sections: list[SourceSection]
    tasks: list[SourceTask]
    relations: list[BlockRelation]
    unassigned_block_ids: list[str]
    review_notes: list[str]


def response_schema(name: str, model: type[SourceModel]) -> dict:
    return {"name": name, "strict": True, "schema": model.model_json_schema()}


def validate_structure(structure: DocumentStructure, block_ids: set[str]) -> None:
    """Only identity, membership and acyclic-parent mechanics; no text matching."""
    def unique_ids(rows, field):
        values = [getattr(row, field) for row in rows]
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError(f"{field} must be nonempty and unique")
        return set(values)

    section_ids = unique_ids(structure.sections, "section_id")
    task_ids = unique_ids(structure.tasks, "task_id")

    def references(values, allowed, label):
        if len(values) != len(set(values)) or not set(values) <= allowed:
            raise ValueError(f"{label} contains duplicate or unknown references")

    def parent_tree(rows, key, parent_key, known):
        parents = {getattr(row, key): getattr(row, parent_key) for row in rows}
        for identity, parent in parents.items():
            if parent and parent not in known:
                raise ValueError(f"{parent_key} refers to unknown identity")
            seen = {identity}
            while parent:
                if parent in seen:
                    raise ValueError(f"cyclic {parent_key}")
                seen.add(parent)
                parent = parents[parent]

    parent_tree(structure.sections, "section_id", "parent_section_id", section_ids)
    parent_tree(structure.tasks, "task_id", "parent_task_id", task_ids)
    ownership = list(structure.unassigned_block_ids)
    for section in structure.sections:
        references(section.block_ids, block_ids, "section block_ids")
        ownership.extend(section.block_ids)
    references(ownership, block_ids, "complete block ownership")
    if set(ownership) != block_ids:
        raise ValueError("every source block needs a section or unassigned disposition")
    for task in structure.tasks:
        if task.section_id and task.section_id not in section_ids:
            raise ValueError("task refers to unknown section_id")
        if not task.prompt_block_ids:
            raise ValueError("task has no source prompt blocks")
        for field in ("prompt_block_ids", "context_block_ids", "answer_block_ids", "figure_block_ids"):
            references(getattr(task, field), block_ids, field)
    for relation in structure.relations:
        references([relation.from_block_id, relation.to_block_id], block_ids, "relation")
