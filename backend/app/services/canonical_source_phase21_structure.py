"""Phase 2.1 chapter-structure and deterministic task-boundary guards."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from . import katex_rules as kr

# MECHANICS ONLY (§3 purge, item 4A): this pattern detects the layout shape of
# a cue Mathpix flattened into plain text — a known banner word alone on the
# first line, body below. It may trigger a LOSSLESS, FLAGGED recovery of the
# block, but the matched cue string never assigns a semantic value
# (source_kind / chapter_wide / activity_origin all ship neutral + flagged
# unless a model verdict ruled them).
_CUE_RE = re.compile(
    r"(?is)^\s*(?P<cue>Discuss|Activity|Project|Write\s+in\s+brief|"
    r"Think\s+about\s+it|Let's\s+discuss)\s*(?:[:\-]\s*)?\n+"
    r"(?P<body>.+?)\s*$"
)
# MECHANICS ONLY: text-span selection inside a block already identified as a
# task (see prompt_from_block). Never a veto on a model-verified recovery and
# never a task/not-task classifier for new content.
_IMPERATIVE_RE = re.compile(
    r"(?i)^(?:with\s+the\s+help\s+of|summari[sz]e|describe|discuss|compare|"
    r"explain|imagine|write|plot|look|examine|identify|find\s+out|trace|"
    r"choose|state|list|calculate|solve|draw|analyse|analyze|interpret|"
    r"comment|who|what|why|how|where|when|which)\b"
)
_MAIN_HEADING_RE = re.compile(
    r"\\section\*?\{\s*(?P<number>\d+)(?:\s+(?P<title>[^}]+))?\s*\}",
    re.IGNORECASE,
)
_SUB_HEADING_RE = re.compile(
    r"\\(?:section|subsection)\*?\{\s*(?P<major>\d+)[.．]"
    r"(?P<minor>\d+)\s+(?P<title>[^}]+)\}",
    re.IGNORECASE,
)
_MD_MAIN_HEADING_RE = re.compile(
    r"(?m)^#{1,2}\s+(?P<number>\d+)(?:\s+(?P<title>.+?))?\s*$"
)
_MD_SUB_HEADING_RE = re.compile(
    r"(?m)^#{1,4}\s+(?P<major>\d+)[.．](?P<minor>\d+)\s+"
    r"(?P<title>.+?)\s*$"
)
_LAYOUT_RE = re.compile(
    r"\\(?:captionsetup|caption|includegraphics|begin\{(?:figure|table|tabular)\}"
    r"|end\{(?:figure|table|tabular)\})",
    re.IGNORECASE,
)
# MECHANICS ONLY (§3 purge, item 4D): matches Mathpix LaTeX layout markup — a
# wire-format fact, not a meaning. The former "new words?" alternative was a
# glossary-vocabulary judgment about what the book means and was removed; the
# non-task-like span break in prompt_from_block still bounds prose tails.
_TASK_CONTAMINATION_RE = re.compile(
    r"(?i)(?:\\begin\{figure\}|\\captionsetup\b|"
    r"\\caption\b|\\includegraphics\b)"
)
_TABLE_HINT_RE = re.compile(
    r"\\hline\b|\\begin\{tabular\}|"
    r"\{\s*\|?(?:[lcrpmbX]\|?){2,}\s*\}",
    re.IGNORECASE,
)
_TABLE_COLUMN_SPEC_RE = re.compile(
    r"\{\s*\|?(?:[lcrpmbX]\|?){2,}\s*\}",
    re.IGNORECASE,
)
# MECHANICS ONLY (§3 purge, item 4B): recognizes the banner shape of a legacy
# plain-cue heading so the LOSSLESS follow-up recovery below can attach
# separately printed sibling prompts to their one parent when NO chapter
# outline exists. Under a model-judged outline the recovery never runs — the
# outline's ruled-task ledger is the only cue authority — and the recovered
# rows always carry a not-model-ruled flag.
_TASK_CUE_HEADING_RE = re.compile(
    r"(?i)^\s*(?:activity|project|write\s+in\s+brief|discuss|"
    r"think\s+about\s+it|let['’]s\s+discuss)\s*$"
)
_FIGURE_REFERENCE_RE = re.compile(
    r"\bfig(?:ure)?s?\.?\s*(?P<number>\d+(?:\.\d+)*"
    r"(?:\s*\([a-z]\))?)",
    re.IGNORECASE,
)


def normal_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def sha256_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def is_task_like(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text and ("?" in text or _IMPERATIVE_RE.match(text)))


def strip_leading_task_cue(value: object, *, label: object = None) -> str:
    """DISPLAY MECHANICS: drop a leading banner line from task text.

    When the model identified the task's visible cue (``label``, e.g. the
    ledger/parser ``source_label``), that exact label is stripped verbatim —
    the hardcoded legacy vocabulary is only a fallback for artifacts that
    carry no model-identified label.
    """
    text = str(value or "").strip()
    label_text = re.sub(r"\s+", " ", str(label or "")).strip()
    if label_text:
        labelled = re.match(
            rf"(?is)^\s*{re.escape(label_text)}\s*(?:[:\-]\s*)?\n+(?P<body>.+)$",
            text,
        )
        if labelled:
            return labelled.group("body").strip()
    match = _CUE_RE.match(text)
    return (match.group("body") if match else text).strip()


_TABULAR_BLOCK_RE = re.compile(
    r"\\begin\{tabular\}\{(?P<spec>[^{}]*)\}(?P<body>.*?)\\end\{tabular\}",
    re.IGNORECASE | re.DOTALL,
)


def flatten_table_markup(value: object) -> str:
    """Legacy pipe-row flattening, kept for TEXT COMPARISON only.

    Display rendering uses ``normalize_task_table_markup`` (KaTeX array);
    block matchers compare flattened cell text because their counterpart
    keys are built from raw table_rows joined with pipes.
    """
    text = str(value or "")
    if not _TABLE_HINT_RE.search(text):
        return text
    text = re.sub(
        r"\\begin\{tabular\}\{[^{}]*\}",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\\end\{tabular\}", "\n", text, flags=re.IGNORECASE)
    text = _TABLE_COLUMN_SPEC_RE.sub("\n", text)
    text = re.sub(r"\\hline\b", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\\\\(?=\s|$)", "\n", text)
    text = re.sub(r"\s*&\s*", " | ", text)
    lines = [
        re.sub(r"[ \t]+", " ", line).strip(" |	")
        for line in text.splitlines()
    ]
    return "\n".join(line for line in lines if line).strip()


def _katex_array_cell(cell: str) -> str:
    """One tabular cell as valid KaTeX array content."""
    cell = re.sub(r"\s+", " ", str(cell or "")).strip()
    if not cell:
        return ""
    # Unwrap inline math delimiters — inside the array everything is math.
    cell = re.sub(r"\\\((.*?)\\\)", r"\1", cell)
    cell = re.sub(r"(?<!\\)\$(.+?)(?<!\\)\$", r"\1", cell)
    # Purely numeric/symbolic cells and TeX commands stay literal math;
    # prose is wrapped so words render as words, not juxtaposed variables.
    if re.fullmatch(r"[0-9.,:%/^*+\-=()\s]+", cell) or re.match(
        r"^\\[A-Za-z]+", cell
    ):
        return cell
    escaped = re.sub(r"([#%&_{}])", r"\\\1", cell)
    return r"\text{" + escaped + "}"


def normalize_task_table_markup(value: object) -> str:
    """Render Mathpix tabular layout as a canonical KaTeX array block.

    Reviewers rejected the earlier pipe-flattened tables: the public wire
    format for a source table is ``[Katex] \\begin{array}{...} ... [/Katex]``
    with every row ruled, so the table survives as a table. Markup the
    block regex cannot parse falls back to the legacy readable flattening.
    """
    text = str(value or "")
    if not _TABLE_HINT_RE.search(text):
        return text

    def _convert(match: re.Match) -> str:
        spec = re.sub(
            r"[^lcr|]", "",
            (match.group("spec") or "").replace("p", "c").replace("X", "c"),
        )
        rows: list[str] = []
        for raw_row in re.split(r"\\\\", match.group("body") or ""):
            row = re.sub(r"\\hline", " ", raw_row, flags=re.IGNORECASE)
            row = re.sub(r"\s+", " ", row).strip()
            if not row:
                continue
            rows.append(
                " & ".join(_katex_array_cell(c) for c in row.split("&"))
            )
        if not rows:
            return " "
        if not spec:
            ncols = max(row.count("&") + 1 for row in rows)
            spec = "|" + "c|" * ncols
        array = (
            r"\begin{array}{" + spec + r"} \hline "
            + r" \\ \hline ".join(rows)
            + r" \\ \hline \end{array}"
        )
        return " [Katex] " + array + " [/Katex] "

    text = _TABULAR_BLOCK_RE.sub(_convert, text)
    # The converted arrays live inside [Katex] tags; mask them so the
    # legacy fallback below only ever touches markup OUTSIDE them.
    masked: list[str] = []

    def _stash(match: re.Match) -> str:
        masked.append(match.group(0))
        return f"{len(masked) - 1}"

    text = re.sub(
        r"\[Katex\].*?\[/Katex\]", _stash, text, flags=re.DOTALL,
    )
    if not _TABLE_HINT_RE.search(text):
        text = re.sub(r"[ \t]+", " ", text)
        for index, block in enumerate(masked):
            text = text.replace(f"{index}", block)
        return text.strip()
    # Legacy fallback for malformed table markup the block regex missed.
    text = re.sub(
        r"\\begin\{tabular\}\{[^{}]*\}",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\\end\{tabular\}", "\n", text, flags=re.IGNORECASE)
    text = _TABLE_COLUMN_SPEC_RE.sub("\n", text)
    text = re.sub(r"\\hline\b", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\\\\(?=\s|$)", "\n", text)
    text = re.sub(r"\s*&\s*", " | ", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip(" |	") for line in text.splitlines()]
    text = "\n".join(line for line in lines if line).strip()
    for index, block in enumerate(masked):
        text = text.replace(f"{index}", block)
    return text


def canonical_task_display(value: object) -> str:
    """Return task text with safe table layout and canonical rich text only."""
    rendered = kr.canonicalize_rich_text(
        normalize_task_table_markup(value)
    ).strip()
    issues = set(kr.rich_text_issues(rendered))
    if issues and issues.issubset({"raw_latex", "raw_math_expression"}):
        repaired = kr.repair_unwrapped_math(rendered)
        if (
            not kr.rich_text_issues(repaired)
            and kr.unwrap_katex(repaired) == kr.unwrap_katex(rendered)
        ):
            rendered = repaired
    return kr.canonicalize_rich_text(rendered).strip()


def _heading_title(block: dict[str, Any]) -> str:
    heading = block.get("heading")
    if isinstance(heading, dict) and heading.get("title"):
        return str(heading.get("title") or "").strip()
    raw = str(block.get("raw_text") or "")
    match = re.search(r"\\(?:sub)*section\*?\{(?P<title>[^}]+)\}", raw)
    return str(match.group("title") if match else raw).strip("# \t\r\n")


def _figure_reference_ids(value: object) -> list[str]:
    found: list[str] = []
    for match in _FIGURE_REFERENCE_RE.finditer(str(value or "")):
        reference = re.sub(r"\s+", "", match.group("number").casefold())
        if reference and reference not in found:
            found.append(reference)
    return found


def recover_followup_task_prompts(canonical: dict[str, Any]) -> int:
    """Attach separately printed prompts to their single source-task parent.

    Mathpix sometimes emits two independently answerable paragraphs below one
    ``Activity`` heading, while the legacy anchor parser retains only the first.
    This pass does not create another canonical parent QID.  It records every
    unowned sibling paragraph beneath the one source parent so the public
    inventory can expose stable leaf Case identities later.

    Recovery is intentionally bounded to a cue-heading region containing
    exactly one canonical task.  Numbered exercise groups already represented
    by several parent QIDs are therefore never collapsed into one parent.

    Cue authority (§3 purge, item 4B): when a chapter outline exists, the
    outline's ruled-task ledger is the ONLY source of task-cue semantics — no
    follow-up region may be opened off the keyword banner match, so the pass
    is skipped entirely (the outline audit already reports task blocks the
    outline never ruled on). Without an outline the banner match survives as
    recovery MECHANICS: the sibling prompt still ships (never lose a
    question), and every recovered row is flagged as not model-ruled.
    """
    if _model_judged_boundaries(canonical):
        return 0
    blocks = [
        block for block in canonical.get("blocks") or []
        if isinstance(block, dict)
    ]
    blocks.sort(key=lambda block: int(block.get("source_start") or 0))
    tasks = [
        task for task in canonical.get("tasks") or []
        if isinstance(task, dict)
    ]
    recovered = 0

    for heading_index, heading in enumerate(blocks):
        if (
            heading.get("kind") != "heading"
            or not _TASK_CUE_HEADING_RE.fullmatch(_heading_title(heading))
        ):
            continue
        region_end = next(
            (
                int(block.get("source_start") or 0)
                for block in blocks[heading_index + 1:]
                if block.get("kind") == "heading"
            ),
            max(
                [int(block.get("source_end") or 0) for block in blocks]
                or [int(heading.get("source_end") or 0)]
            ),
        )
        region_start = int(heading.get("source_end") or 0)
        owners = [
            task for task in tasks
            if region_start <= int(task.get("source_start") or 0) < region_end
        ]
        if len(owners) != 1:
            continue
        owner = owners[0]
        owner_start = int(owner.get("source_start") or 0)
        seen_task_content = False

        for block in blocks[heading_index + 1:]:
            block_start = int(block.get("source_start") or 0)
            if block_start >= region_end:
                break
            if block.get("kind") not in {"paragraph", "list"}:
                continue
            prompt = prompt_from_block(block.get("raw_text") or "")
            if not is_task_like(prompt):
                if seen_task_content:
                    break
                continue
            seen_task_content = True
            if block_start < owner_start or task_matches_prompt(owner, prompt):
                continue
            if any(task_matches_prompt(task, prompt) for task in tasks):
                continue
            followups = owner.setdefault("source_followup_prompts", [])
            if any(
                normal_text(row.get("raw_prompt")) == normal_text(prompt)
                for row in followups if isinstance(row, dict)
            ):
                continue
            followups.append({
                "raw_prompt": prompt,
                "display_prompt": canonical_task_display(prompt),
                "source_start": block_start,
                "source_end": int(block.get("source_end") or block_start + len(prompt)),
                "source_section_index": int(owner.get("source_section_index") or 0),
                "source_position": max(
                    0,
                    block_start - int(
                        next(
                            (
                                section.get("source_start") or 0
                                for section in canonical.get("sections") or []
                                if isinstance(section, dict)
                                and section.get("section_id") == block.get("section_id")
                            ),
                            0,
                        )
                    ),
                ),
                "section_id": str(block.get("section_id") or owner.get("section_id") or ""),
                "block_id": str(block.get("block_id") or ""),
                "explicit_figure_reference_ids": _figure_reference_ids(prompt),
                "recovery": "phase21_same_cue_followup_prompt",
                # The banner that opened this region was matched by shape,
                # not ruled by a model — reviewers see the provenance.
                "cue_ruling": "not_model_ruled_flagged",
            })
            recovered += 1
    return recovered


def _resolved_leaf_figures(
    canonical: dict[str, Any],
    *,
    prompt: str,
    inherited: dict[str, Any] | None = None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    references = _figure_reference_ids(prompt)
    if inherited is not None:
        inherited_raw = _figure_reference_ids(
            inherited.get("raw_prompt") or inherited.get("display_prompt") or ""
        )
        if references == inherited_raw:
            return (
                list(inherited.get("figure_refs") or []),
                list(inherited.get("image_urls") or []),
                list(inherited.get("unresolved_figure_reference_ids") or []),
                list(inherited.get("ambiguous_figure_reference_ids") or []),
            )

    by_reference: dict[str, list[dict[str, Any]]] = {}
    for figure in canonical.get("figures") or []:
        if not isinstance(figure, dict):
            continue
        for reference in figure.get("reference_ids") or []:
            key = re.sub(r"\s+", "", str(reference or "").casefold())
            if key:
                by_reference.setdefault(key, []).append(figure)
    figure_refs: list[str] = []
    image_urls: list[str] = []
    unresolved: list[str] = []
    ambiguous: list[str] = []
    for reference in references:
        candidates = by_reference.get(reference, [])
        selected: dict[str, Any] | None = None
        if len(candidates) == 1:
            selected = candidates[0]
        elif len(candidates) > 1:
            # A caption may mention another panel in explanatory prose (for
            # example Fig. 14(b) says "seen in Fig. 14(a)").  That does not
            # make ownership of 14(a) ambiguous when exactly one Figure has
            # 14(a) as its primary, first canonical reference.
            primary = []
            for figure in candidates:
                ordered = [
                    re.sub(r"\s+", "", str(value or "").casefold())
                    for value in figure.get("reference_ids") or []
                    if str(value or "").strip()
                ]
                if ordered and ordered[0] == reference:
                    primary.append(figure)
            if len(primary) == 1:
                selected = primary[0]
        if selected is not None:
            figure = selected
            figure_id = str(figure.get("figure_id") or "")
            if figure_id and figure_id not in figure_refs:
                figure_refs.append(figure_id)
            for url in figure.get("image_urls") or []:
                url = str(url or "").strip()
                if url and url not in image_urls:
                    image_urls.append(url)
        elif not candidates:
            unresolved.append(reference)
        else:
            ambiguous.append(reference)
    return figure_refs, image_urls, unresolved, ambiguous


def _model_judged_boundaries(canonical: dict[str, Any]) -> bool:
    """Whether a chapter-outline pass decided this source's question boundaries.

    Its presence means a model read the whole chapter and ruled on every
    task; deterministic enumeration must not offer a competing answer.
    """
    outline = canonical.get("chapter_outline")
    return isinstance(outline, dict) and bool(outline.get("version"))


def materialize_task_leaf_cases(canonical: dict[str, Any]) -> int:
    """Create stable inventory leaves without replacing canonical parents."""
    total_leaves = 0
    decomposed_parents = 0
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        task.pop("leaf_cases", None)
        parent_qid = str(task.get("qid") or "").strip()
        parent_identity = str(task.get("identity_key") or "")
        sources: list[dict[str, Any]] = [{
            "raw_prompt": str(task.get("raw_prompt") or ""),
            "display_prompt": str(task.get("display_prompt") or ""),
            "source_start": int(task.get("source_start") or 0),
            "source_end": int(task.get("source_end") or 0),
            "source_section_index": int(task.get("source_section_index") or 0),
            "source_position": int(task.get("source_position") or 0),
            "section_id": str(task.get("section_id") or ""),
            "block_id": "",
            "base_task": True,
        }]
        sources.extend(
            row for row in task.get("source_followup_prompts") or []
            if isinstance(row, dict)
        )

        gpt_parts = [
            part for part in task.get("gpt_boundary_parts") or []
            if isinstance(part, dict) and str(part.get("text") or "").strip()
        ]
        if not gpt_parts and _model_judged_boundaries(canonical):
            # Under model-judged boundaries the outline pass is the only
            # authority on whether a question splits. Running the
            # enumeration heuristics here would compute leaves that the
            # inventory then discards — wasted work that reads like a second
            # opinion. A task the model left whole stays whole.
            continue

        provisional: list[dict[str, Any]] = []
        if gpt_parts:
            # The chapter-outline judge decided these subparts are independent
            # questions (board conventions differ; the model judged content,
            # not typography). Its verbatim parts replace the enumeration
            # heuristics entirely for this task.
            inherited_context = str(task.get("shared_context") or "").strip()
            base = sources[0]
            for part in gpt_parts:
                stem = str(part.get("stem") or "").strip()
                raw_part = str(part.get("text") or "").strip()
                leaf_context = "\n\n".join(
                    value for value in (inherited_context, stem) if value
                )
                provisional.append({
                    **base,
                    "base_task": False,
                    "raw_prompt": raw_part,
                    "display_prompt": canonical_task_display(
                        " ".join(value for value in (stem, raw_part) if value)
                    ),
                    "shared_context": leaf_context,
                    "requires_context": bool(leaf_context),
                    "subpart_label": str(part.get("label") or "").strip(),
                    "decomposition": "gpt_semantic_boundary",
                })
            sources = []

        # §3 purge, item 4D: the deterministic enumeration and visual-cluster
        # splitters are gone. A task the model left whole stays whole
        # (never-split doctrine); the only in-parent decomposition left is a
        # gpt_boundary_parts verdict. Follow-up prompts recovered from
        # SEPARATE source blocks still become leaves so the whole-item merge
        # in inventory_from_canonical can carry their wording and visuals —
        # identity bookkeeping, never a deterministic re-split.
        for source in sources:
            raw_prompt = str(source.get("raw_prompt") or "").strip()
            provisional.append({
                **source,
                "raw_prompt": raw_prompt,
                "display_prompt": str(
                    source.get("display_prompt")
                    or canonical_task_display(raw_prompt)
                ),
                "shared_context": str(task.get("shared_context") or ""),
                "requires_context": bool(task.get("requires_context")),
                "subpart_label": str(task.get("subpart_label") or ""),
            })

        if len(provisional) <= 1:
            continue
        leaves: list[dict[str, Any]] = []
        for leaf_index, leaf in enumerate(provisional, start=1):
            leaf_qid = f"{parent_qid}.{leaf_index}"
            is_base = bool(leaf.get("base_task")) and not leaf.get("subpart_label")
            figures, urls, unresolved, ambiguous = _resolved_leaf_figures(
                canonical,
                # The visual reference is commonly carried by the shared
                # instruction rather than repeated in every enumerated item.
                prompt="\n\n".join(
                    part for part in (
                        str(leaf.get("shared_context") or "").strip(),
                        str(leaf.get("display_prompt") or "").strip(),
                        str(leaf.get("raw_prompt") or "").strip(),
                    )
                    if part
                ),
                inherited=task if is_base else None,
            )
            identity_material = "\u241f".join([
                parent_identity,
                str(leaf.get("subpart_label") or leaf_index),
                str(leaf.get("raw_prompt") or ""),
                str(leaf.get("source_start") or 0),
            ])
            leaves.append({
                **{
                    key: task.get(key)
                    for key in (
                        "source_kind", "source_label", "parent_source_label",
                        "topic_hint", "page_hint", "chapter_wide",
                        "activity_origin", "_topic_scope", "content_objects",
                        "raw_solution_or_answer",
                    )
                    if task.get(key) not in (None, "", [], {})
                },
                "qid": leaf_qid,
                "case_id": f"CASE-{leaf_qid}",
                "parent_qid": parent_qid,
                "parent_task_id": str(task.get("task_id") or ""),
                "parent_identity_key": parent_identity,
                "identity_key": sha256_text(identity_material),
                "leaf_order": leaf_index,
                "raw_prompt": str(leaf.get("raw_prompt") or ""),
                "display_prompt": str(leaf.get("display_prompt") or ""),
                "shared_context": str(leaf.get("shared_context") or ""),
                "requires_context": bool(leaf.get("requires_context")),
                "subpart_label": str(leaf.get("subpart_label") or ""),
                "source_start": int(leaf.get("source_start") or 0),
                "source_end": int(leaf.get("source_end") or 0),
                "source_section_index": int(leaf.get("source_section_index") or 0),
                "source_position": int(leaf.get("source_position") or 0),
                "section_id": str(leaf.get("section_id") or task.get("section_id") or ""),
                "source_block_id": str(leaf.get("block_id") or ""),
                "requires_visual": bool(figures or urls or unresolved or ambiguous),
                "figure_refs": figures,
                "image_urls": urls,
                "_image_captions": {},
                "explicit_figure_reference_ids": _figure_reference_ids(
                    "\n\n".join(
                        part for part in (
                            str(leaf.get("shared_context") or "").strip(),
                            str(leaf.get("display_prompt") or "").strip(),
                            str(leaf.get("raw_prompt") or "").strip(),
                        )
                        if part
                    )
                ),
                "unresolved_figure_reference_ids": unresolved,
                "ambiguous_figure_reference_ids": ambiguous,
                "canonical_source_mode": task.get("canonical_source_mode"),
                "decomposition": str(leaf.get("decomposition") or ""),
            })
        task["leaf_cases"] = leaves
        task["inventory_leaf_count"] = len(leaves)
        decomposed_parents += 1
        total_leaves += len(leaves)

    parent_count = len([
        task for task in canonical.get("tasks") or [] if isinstance(task, dict)
    ])
    inventory_count = parent_count + total_leaves - decomposed_parents
    statistics = canonical.setdefault("statistics", {})
    if isinstance(statistics, dict):
        statistics["parent_tasks"] = parent_count
        statistics["decomposed_parent_tasks"] = decomposed_parents
        statistics["inventory_leaf_tasks"] = inventory_count
    source_contract = canonical.setdefault("source_contract", {})
    if isinstance(source_contract, dict):
        source_contract["parent_task_count"] = parent_count
        source_contract["inventory_item_count"] = inventory_count
        source_contract["decomposed_parent_task_count"] = decomposed_parents
    hardening = canonical.get("phase21_hardening")
    if isinstance(hardening, dict):
        hardening["parent_task_count"] = parent_count
        hardening["inventory_item_count"] = inventory_count
        hardening["decomposed_parent_task_count"] = decomposed_parents
    return inventory_count


def inventory_shape(canonical: dict[str, Any]) -> tuple[int, int, int]:
    """Return parent, decomposed-parent, and effective inventory counts."""
    tasks = [
        task for task in canonical.get("tasks") or [] if isinstance(task, dict)
    ]
    decomposed = 0
    total_leaves = 0
    for task in tasks:
        leaves = [
            leaf for leaf in task.get("leaf_cases") or []
            if isinstance(leaf, dict)
        ]
        if leaves:
            decomposed += 1
            total_leaves += len(leaves)
    return len(tasks), decomposed, len(tasks) + total_leaves - decomposed


def followup_prompt_count(canonical: dict[str, Any]) -> int:
    return sum(
        len([
            row for row in task.get("source_followup_prompts") or []
            if isinstance(row, dict)
        ])
        for task in canonical.get("tasks") or []
        if isinstance(task, dict)
    )


def inventory_contract_issues(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    """Fail closed when derived leaf Cases or their count seals are stale."""
    issues: list[dict[str, Any]] = []
    parent_count, decomposed_count, inventory_count = inventory_shape(canonical)

    # Derivation seal (mechanics, no content judgment): leaf Cases are
    # DERIVED from inputs the ledger already records — the outline judge's
    # gpt_boundary_parts and the recovered source_followup_prompts. A
    # persisted artifact whose leaves no longer match those inputs has
    # silently lost or duplicated a learner question, so it must fail closed
    # and be rebuilt from source.
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        gpt_parts = [
            part for part in task.get("gpt_boundary_parts") or []
            if isinstance(part, dict) and str(part.get("text") or "").strip()
        ]
        followups = [
            row for row in task.get("source_followup_prompts") or []
            if isinstance(row, dict)
        ]
        if gpt_parts:
            expected_leaves = len(gpt_parts)
        elif _model_judged_boundaries(canonical):
            expected_leaves = 0
        else:
            expected_leaves = 1 + len(followups) if followups else 0
        actual_leaves = len([
            leaf for leaf in task.get("leaf_cases") or []
            if isinstance(leaf, dict)
        ])
        if actual_leaves != expected_leaves:
            issues.append({
                "severity": "error",
                "code": "phase21_leaf_derivation_stale",
                "message": (
                    "A task's derived leaf Cases no longer match the recorded "
                    "model verdicts and recovered follow-up prompts."
                ),
                "qid": str(task.get("qid") or ""),
                "expected_leaf_count": expected_leaves,
                "actual_leaf_count": actual_leaves,
            })

    expected = {
        "parent_task_count": parent_count,
        "decomposed_parent_task_count": decomposed_count,
        "inventory_item_count": inventory_count,
    }
    containers = (
        ("source_contract", canonical.get("source_contract")),
        ("phase21_hardening", canonical.get("phase21_hardening")),
    )
    for container_name, container in containers:
        if not isinstance(container, dict):
            continue
        for field, actual in expected.items():
            if field not in container:
                continue
            try:
                sealed = int(container.get(field))
            except (TypeError, ValueError):
                sealed = -1
            if sealed != actual:
                issues.append({
                    "severity": "error",
                    "code": "phase21_inventory_count_seal_mismatch",
                    "message": (
                        "A derived inventory count seal does not match the "
                        "canonical parent/leaf Case topology."
                    ),
                    "container": container_name,
                    "field": field,
                    "expected": actual,
                    "actual": container.get(field),
                })
    statistics = canonical.get("statistics")
    if isinstance(statistics, dict):
        for field, actual in {
            "parent_tasks": parent_count,
            "decomposed_parent_tasks": decomposed_count,
            "inventory_leaf_tasks": inventory_count,
        }.items():
            if field not in statistics:
                continue
            try:
                sealed = int(statistics.get(field))
            except (TypeError, ValueError):
                sealed = -1
            if sealed != actual:
                issues.append({
                    "severity": "error",
                    "code": "phase21_inventory_count_seal_mismatch",
                    "message": (
                        "A derived inventory statistics seal does not match "
                        "the canonical parent/leaf Case topology."
                    ),
                    "container": "statistics",
                    "field": field,
                    "expected": actual,
                    "actual": statistics.get(field),
                })
    return issues


def prompt_from_block(value: object, *, label: object = None) -> str:
    """Extract only the contiguous task prompt from one source block.

    MECHANICS ONLY: text-span selection for boundary repair and follow-up
    matching. The former glossary vocabulary (``_GLOSSARY_RE``) judged what
    the book means and was removed (§3 purge, item 4D); the span still stops
    at LaTeX layout markup (wire-format fact) and at the first
    non-question/non-imperative line, which never decides a block's kind.
    """
    text = strip_leading_task_cue(value, label=label)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    start = next(
        (index for index, line in enumerate(lines) if is_task_like(line)),
        None,
    )
    if start is None:
        return ""
    chosen: list[str] = []
    for line in lines[start:]:
        if chosen and _LAYOUT_RE.search(line):
            break
        if chosen and not is_task_like(line):
            break
        chosen.append(line)
    return " ".join(chosen).strip()


def numbered_heading_inventory(
    canonical: dict[str, Any],
) -> tuple[dict[int, dict], dict[int, list[dict]]]:
    mains: dict[int, dict] = {}
    subsections: dict[int, list[dict]] = {}
    for block in canonical.get("blocks") or []:
        if not isinstance(block, dict) or block.get("kind") != "heading":
            continue
        raw = str(block.get("raw_text") or "")
        main = _MAIN_HEADING_RE.search(raw) or _MD_MAIN_HEADING_RE.search(raw)
        if main:
            mains[int(main.group("number"))] = block
        sub = _SUB_HEADING_RE.search(raw) or _MD_SUB_HEADING_RE.search(raw)
        if sub:
            subsections.setdefault(int(sub.group("major")), []).append(block)
    # Phase 2.2 may recover an omitted parent heading from the original PDF.
    # Keep it outside ``blocks`` so raw-MMD reconstruction remains byte-exact,
    # but expose a block-shaped semantic entry to every existing hierarchy rule.
    for section in canonical.get("sections") or []:
        if not isinstance(section, dict):
            continue
        adjudicated = section.get("adjudicated_heading")
        if not isinstance(adjudicated, dict):
            continue
        try:
            number = int(adjudicated.get("section_number"))
        except (TypeError, ValueError):
            continue
        title = str(adjudicated.get("title") or section.get("title") or "").strip()
        if not number or not title:
            continue
        mains.setdefault(number, {
            "block_id": f"ADJUDICATED-{section.get('section_id') or number}",
            "section_id": section.get("section_id"),
            "source_start": int(section.get("source_start") or 0),
            "source_end": int(section.get("source_end") or 0),
            "heading": {
                "title": title,
                "level": 1,
                "heading_kind": "adjudicated_pdf",
            },
            "raw_text": str(adjudicated.get("raw_text") or ""),
            "adjudicated_heading": True,
        })
    return mains, subsections


def section_integrity_issues(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    mains, subsections = numbered_heading_inventory(canonical)
    issues: list[dict[str, Any]] = []
    # Some textbooks number only subsections using the external chapter number
    # (for example 11.1–11.8 under an unnumbered "Electricity" heading). A
    # missing parent is provable only after the source establishes at least one
    # numbered main-section sequence of its own.
    if not mains:
        return issues
    for major, blocks in sorted(subsections.items()):
        if major in mains:
            continue
        issues.append({
            "severity": "error",
            "code": "phase21_missing_numbered_parent_section",
            "message": (
                f"Subsections {major}.x exist, but the numbered Section {major} "
                "heading is missing from the Mathpix MMD."
            ),
            "section_number": major,
            "subsection_block_ids": [block.get("block_id") for block in blocks],
            "source_start": min(
                int(block.get("source_start") or 0) for block in blocks
            ),
        })
    numbers = sorted(mains)
    if numbers[0] == 1:
        for number in range(1, numbers[-1] + 1):
            if number not in mains:
                issues.append({
                    "severity": "error",
                    "code": "phase21_numbered_section_gap",
                    "message": (
                        f"The numbered chapter sequence jumps over Section {number}."
                    ),
                    "section_number": number,
                })
    return issues


def topic_for_position(canonical: dict[str, Any], position: int) -> str:
    mains, _subsections = numbered_heading_inventory(canonical)
    ordered = sorted(
        (
            int(block.get("source_start") or 0),
            str((block.get("heading") or {}).get("title") or "").strip(),
        )
        for block in mains.values()
    )
    if not ordered:
        return ""
    prior = [title for start, title in ordered if start <= position and title]
    return prior[-1] if prior else ordered[0][1]


def task_matches_prompt(task: dict[str, Any], prompt: str) -> bool:
    prompt_key = normal_text(prompt)
    if not prompt_key:
        return False
    for field in ("raw_prompt", "display_prompt"):
        task_key = normal_text(task.get(field) or "")
        if not task_key:
            continue
        if task_key == prompt_key:
            return True
        if min(len(task_key), len(prompt_key)) >= 30 and (
            task_key in prompt_key or prompt_key in task_key
        ):
            return True
    return False


def recover_plain_task_cues(canonical: dict[str, Any]) -> int:
    """Recover cues emitted by Mathpix as plain text inside a source block.

    §3 purge, item 4A: the matched cue STRING no longer assigns any semantic
    value. Every recovered task ships LOSSLESS (the whole block body,
    verbatim, minus only the banner line) with a NEUTRAL
    ``source_kind="checkpoint_question"``, ``chapter_wide=False``,
    ``activity_origin=False`` and an explicit not-model-ruled flag. The
    chapter outline, where one exists, is the only authority that could rule
    a cue's kind — its ledger is keyed by PDF page references and therefore
    never reaches these MMD blocks, so a block the outline did not rule is
    recovered flagged rather than defaulted or dropped (never lose a
    learner question).
    """
    tasks = [
        task for task in canonical.get("tasks") or [] if isinstance(task, dict)
    ]
    sections = {
        str(section.get("section_id") or ""): section
        for section in canonical.get("sections") or []
        if isinstance(section, dict)
    }
    outline_present = _model_judged_boundaries(canonical)
    recovered = 0
    for block in canonical.get("blocks") or []:
        if (
            not isinstance(block, dict)
            or block.get("kind") not in {"paragraph", "list"}
        ):
            continue
        match = _CUE_RE.match(str(block.get("raw_text") or ""))
        if not match:
            continue
        cue = str(match.group("cue") or "").strip()
        # Lossless recovery: the WHOLE block body, verbatim, after removing
        # only the banner line the cue printed above it (display mechanics).
        prompt = strip_leading_task_cue(
            block.get("raw_text") or "", label=cue
        )
        if not prompt.strip() or any(
            task_matches_prompt(task, prompt) for task in tasks
        ):
            continue
        section_id = str(block.get("section_id") or "")
        section = sections.get(section_id, {})
        start_in_block = str(block.get("raw_text") or "").find(match.group("body"))
        source_start = int(block.get("source_start") or 0) + max(0, start_in_block)
        review_flag = (
            "task cue was not ruled by the chapter outline; recovered with a "
            "neutral kind and flagged for review"
            if outline_present
            else "no model verdict ruled this task cue; recovered with a "
            "neutral kind and flagged for review"
        )
        tasks.append({
            "task_id": "",
            "qid": "",
            "order": 0,
            "order_index": 0,
            # Neutral, flagged — never derived from the cue wording.
            "source_kind": "checkpoint_question",
            "source_label": cue,
            "parent_source_label": cue,
            "topic_hint": topic_for_position(canonical, source_start),
            "raw_prompt": prompt,
            "display_prompt": canonical_task_display(prompt),
            "identity_key": "",
            "section_id": section_id,
            "source_section_index": max(0, int(section.get("order") or 1) - 1),
            "source_position": max(
                0, source_start - int(section.get("source_start") or 0)
            ),
            "source_start": source_start,
            "source_end": int(block.get("source_end") or source_start + len(prompt)),
            "source_location_confidence": "phase21_plain_task_cue",
            "source_kind_ruling": "not_model_ruled_flagged",
            "review_flags": [review_flag],
            "chapter_wide": False,
            "activity_origin": False,
            "requires_visual": False,
            "requires_context": False,
            "image_urls": [],
            "figure_refs": [],
            "explicit_figure_reference_ids": [],
            "unresolved_figure_reference_ids": [],
            "ambiguous_figure_reference_ids": [],
            "display_overrides": [],
            "phase21_recovery": {
                "reason": "plain_text_task_cue",
                "block_id": block.get("block_id"),
                "cue_ruling": "not_model_ruled_flagged",
            },
        })
        recovered += 1
    canonical["tasks"] = tasks
    return recovered


def related_task_blocks(
    canonical: dict[str, Any], task: dict[str, Any]
) -> list[dict[str, Any]]:
    task_id = str(task.get("task_id") or "")
    start = int(task.get("source_start") or 0)
    end = int(task.get("source_end") or start)
    related: list[dict[str, Any]] = []
    for block in canonical.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if task_id and task_id in (block.get("task_ids") or []):
            related.append(block)
            continue
        block_start = int(block.get("source_start") or 0)
        block_end = int(block.get("source_end") or block_start)
        if start < block_end and end > block_start:
            related.append(block)
    return related


def _block_shared_with_other_tasks(
    canonical: dict[str, Any], task: dict[str, Any], block: dict[str, Any]
) -> bool:
    block_start = int(block.get("source_start") or 0)
    block_end = int(block.get("source_end") or block_start)
    for other in canonical.get("tasks") or []:
        if other is task or not isinstance(other, dict):
            continue
        start = int(other.get("source_start") or 0)
        end = int(other.get("source_end") or start)
        if start < block_end and end > block_start:
            return True
    return False


def trim_task_boundaries(canonical: dict[str, Any]) -> int:
    """Remove glossary, narrative and layout tails swallowed by a task."""
    repaired = 0
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        model_label = task.get("source_label")
        candidates: list[tuple[int, int, str, dict[str, Any]]] = []
        for block in related_task_blocks(canonical, task):
            if block.get("kind") not in {"paragraph", "list"}:
                continue
            # A block whose span hosts other tasks (an exercise list holds
            # many numbered questions) cannot be this one task's boundary:
            # rewriting to it collapses distinct questions into one prompt
            # and silently loses the rest. Positional bookkeeping only.
            if _block_shared_with_other_tasks(canonical, task, block):
                continue
            prompt = prompt_from_block(
                block.get("raw_text") or "", label=model_label
            )
            if is_task_like(prompt):
                candidates.append((
                    int(block.get("source_start") or 0),
                    len(prompt),
                    prompt,
                    block,
                ))
        if not candidates:
            continue
        candidates.sort(key=lambda item: (item[0], item[1]))
        _start, _length, candidate, block = candidates[0]
        current = strip_leading_task_cue(
            task.get("raw_prompt") or "", label=model_label
        )
        current_key = normal_text(current)
        candidate_key = normal_text(candidate)
        polluted = bool(_TASK_CONTAMINATION_RE.search(current))
        contained = bool(
            candidate_key and candidate_key != current_key and candidate_key in current_key
        )
        overly_long = len(current) > max(
            len(candidate) + 160, int(len(candidate) * 1.8)
        )
        # STRUCTURAL GUARD (§3 purge, item 4): a prompt whose location was
        # anchored verbatim in the source, or verified against the PDF page,
        # already has its true boundaries — only layout-markup contamination
        # (mechanics) justifies rewriting it. Keying this on the task's kind
        # vocabulary would be a semantic judgment; provenance is not.
        location = str(task.get("source_location_confidence") or "").strip()
        anchored = location in {
            "exact_text_match",
            "phase22_verified_pdf_page",
            "gpt_pdf_acsd_verified_block",
        }
        if anchored and not polluted:
            continue
        if not (polluted or contained or overly_long or current.startswith("## ")):
            continue
        task.setdefault("phase21_boundary_repairs", []).append({
            "reason": "removed_non_task_source_tail",
            "block_id": block.get("block_id"),
            "raw_prompt_sha256": sha256_text(task.get("raw_prompt") or ""),
        })
        task.setdefault("raw_prompt_original", task.get("raw_prompt") or "")
        task["raw_prompt"] = candidate
        task["display_prompt"] = canonical_task_display(candidate)
        task["source_start"] = int(
            block.get("source_start") or task.get("source_start") or 0
        )
        task["source_end"] = int(
            block.get("source_end") or task.get("source_end") or 0
        )
        task["source_location_confidence"] = "phase21_prompt_block"
        repaired += 1
    return repaired


def renumber_tasks(canonical: dict[str, Any]) -> None:
    tasks = [
        task for task in canonical.get("tasks") or [] if isinstance(task, dict)
    ]
    had_leaf_cases = any(task.get("leaf_cases") for task in tasks)
    tasks.sort(key=lambda task: (
        int(task.get("source_start") or 0),
        int(task.get("source_position") or 0),
        normal_text(task.get("raw_prompt") or ""),
    ))
    old_to_new: dict[str, str] = {}
    for order, task in enumerate(tasks, start=1):
        old_id = str(task.get("task_id") or "")
        new_id = f"TASK-{order:05d}"
        if old_id:
            old_to_new[old_id] = new_id
        task["task_id"] = new_id
        task["qid"] = f"QINV-{order:04d}"
        task["order"] = order
        task["order_index"] = order
        material = "\u241f".join([
            str(task.get("section_id") or ""),
            str(task.get("source_start") or 0),
            str(task.get("raw_prompt") or ""),
        ])
        task["identity_key"] = sha256_text(material)
    for block in canonical.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        previous = [str(value) for value in block.get("task_ids") or [] if value]
        if previous:
            block.setdefault("source_task_ids", previous)
        block["task_ids"] = [
            old_to_new[value] for value in previous if value in old_to_new
        ]
    for task in tasks:
        start = int(task.get("source_start") or 0)
        end = int(task.get("source_end") or start)
        for block in canonical.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            block_start = int(block.get("source_start") or 0)
            block_end = int(block.get("source_end") or block_start)
            if start < block_end and end > block_start:
                ids = block.setdefault("task_ids", [])
                if task["task_id"] not in ids:
                    ids.append(task["task_id"])
    canonical["tasks"] = tasks
    if isinstance(canonical.get("statistics"), dict):
        canonical["statistics"]["tasks"] = len(tasks)
    if had_leaf_cases:
        # Phase 2.2 may insert a PDF-verified parent task before an existing
        # decomposed parent. Rebuild derived leaf qids from the newly canonical
        # parent sequence so no stale QINV prefix can survive adjudication.
        materialize_task_leaf_cases(canonical)


def task_boundary_issues(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    leaf_qids: set[str] = set()
    leaf_identities: set[str] = set()
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        if _TASK_CONTAMINATION_RE.search(str(task.get("raw_prompt") or "")):
            issues.append({
                "severity": "error",
                "code": "phase21_task_boundary_contamination",
                "message": (
                    "A canonical task still contains Mathpix layout markup."
                ),
                "qid": task.get("qid") or "",
            })
        leaves = [
            leaf for leaf in task.get("leaf_cases") or []
            if isinstance(leaf, dict)
        ]
        if not leaves:
            continue
        parent_qid = str(task.get("qid") or "").strip()
        if len(leaves) < 2:
            issues.append({
                "severity": "error",
                "code": "phase21_leaf_case_decomposition_not_plural",
                "message": "A decomposed canonical parent must expose at least two leaves.",
                "qid": parent_qid,
            })
        for index, leaf in enumerate(leaves, start=1):
            leaf_qid = str(leaf.get("qid") or "").strip()
            identity = str(leaf.get("identity_key") or "").strip()
            expected_qid = f"{parent_qid}.{index}"
            invalid = bool(
                leaf_qid != expected_qid
                or str(leaf.get("parent_qid") or "").strip() != parent_qid
                or not identity
                or leaf_qid in leaf_qids
                or identity in leaf_identities
                or not str(leaf.get("raw_prompt") or "").strip()
            )
            if invalid:
                issues.append({
                    "severity": "error",
                    "code": "phase21_leaf_case_identity_mismatch",
                    "message": (
                        "A canonical inventory leaf lost stable parent, order, "
                        "identity, or source-prompt provenance."
                    ),
                    "qid": leaf_qid or expected_qid,
                    "parent_qid": parent_qid,
                })
            leaf_qids.add(leaf_qid)
            leaf_identities.add(identity)
    if not _model_judged_boundaries(canonical):
        # Cue coverage under NO outline: every plain-cue block the recovery
        # mechanics can see must map to exactly one canonical task. Under a
        # model-judged outline this deterministic ledger stands down — the
        # outline's ruled-task audit (unruled_task_refs) is the coverage
        # authority there.
        for block in canonical.get("blocks") or []:
            if (
                not isinstance(block, dict)
                or block.get("kind") not in {"paragraph", "list"}
            ):
                continue
            match = _CUE_RE.match(str(block.get("raw_text") or ""))
            if not match:
                continue
            prompt = strip_leading_task_cue(
                block.get("raw_text") or "",
                label=str(match.group("cue") or "").strip(),
            )
            owners = [
                task for task in canonical.get("tasks") or []
                if isinstance(task, dict) and task_matches_prompt(task, prompt)
            ]
            if len(owners) != 1:
                issues.append({
                    "severity": "error",
                    "code": "phase21_task_cue_coverage_mismatch",
                    "message": (
                        "A source task cue does not map to exactly one canonical task."
                    ),
                    "block_id": block.get("block_id"),
                    "owner_count": len(owners),
                })
    return issues
