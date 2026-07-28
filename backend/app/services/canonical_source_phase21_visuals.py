"""Phase 2.1 visual ownership, context linking and source-figure guards."""
from __future__ import annotations

import copy
import re
import unicodedata
from collections import Counter
from typing import Any

from . import katex_rules as kr
from . import canonical_source_phase21_structure as structure

_FIGURE_REFERENCE_RE = re.compile(
    r"\bfig(?:ure)?s?\.?\s*(?P<number>\d+(?:\.\d+)*"
    r"(?:\s*\([a-z]\))?)",
    re.IGNORECASE,
)
_IMAGE_TAG_RE = re.compile(
    r'\[img\s+src="(?P<src>https?://[^"]+)"\s+alt="(?P<alt>[^"]*)"'
    r"[^\]]*\]",
    re.IGNORECASE,
)
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(\s*(?P<src>https?://(?:[^()\s]|(?:\([^()]*\)))+)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)",
    re.IGNORECASE,
)
_LAYOUT_RE = re.compile(
    r"\\(?:captionsetup|caption|includegraphics|begin\{(?:figure|table|tabular)\}"
    r"|end\{(?:figure|table|tabular)\})",
    re.IGNORECASE,
)
_BOX_REF_RE = re.compile(r"\bbox\s*(?P<number>\d+)\b", re.IGNORECASE)
_CONTEXT_CUE_RE = re.compile(
    r"(?i)\b(?:with\s+the\s+help\s+of|cited\s+above|viewpoint\s+of\s+the\s+"
    r"journalist|chart|table)\b"
)
_VISUAL_WORDS: dict[str, frozenset[str]] = {
    "map": frozenset({"map"}),
    "caricature": frozenset({"caricature", "cartoon"}),
    "print": frozenset({"print"}),
    "painting": frozenset({"painting", "painted", "portrait"}),
    "chart": frozenset({"chart", "table"}),
    "graph": frozenset({"graph"}),
    "diagram": frozenset({"diagram"}),
    "photograph": frozenset({"photograph", "photo"}),
}
_PERSON_NAME_SEQUENCE_RE = re.compile(
    r"\b(?:[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]{2,}\s+){1,3}"
    r"(?P<surname>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]{3,})\b"
)
_PERSON_SURNAME_STOPWORDS = frozenset({
    "activity", "allegory", "britannia", "congress", "europe", "figure",
    "frankfurt", "germania", "italia", "marianne", "parliament",
    "republic", "source", "visualising", "vienna",
})


def unique(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def figure_reference_ids(value: object) -> list[str]:
    found: list[str] = []
    for match in _FIGURE_REFERENCE_RE.finditer(str(value or "")):
        figure_id = re.sub(r"\s+", "", match.group("number").casefold())
        if figure_id and figure_id not in found:
            found.append(figure_id)
    return found


def visual_kinds(value: object) -> set[str]:
    text = structure.normal_text(value)
    kinds: set[str] = set()
    for kind, words in _VISUAL_WORDS.items():
        if any(re.search(rf"\b{re.escape(word)}s?\b", text) for word in words):
            kinds.add(kind)
    return kinds


def visuals_compatible(prompt: str, caption: str) -> bool:
    prompt_kinds = visual_kinds(prompt)
    caption_kinds = visual_kinds(caption)
    if not prompt_kinds:
        return True
    if (
        "map" in prompt_kinds
        and "caricature" in caption_kinds
        and "caricature" not in prompt_kinds
    ):
        return False
    if (
        "caricature" in prompt_kinds
        and "map" in caption_kinds
        and "map" not in prompt_kinds
    ):
        return False
    if caption_kinds and prompt_kinds.isdisjoint(caption_kinds):
        generic = {"print", "painting", "photograph"}
        if not (prompt_kinds & generic and caption_kinds & generic):
            return False
    return True


def figure_maps(
    canonical: dict[str, Any],
) -> tuple[dict[str, dict], dict[str, dict], dict[str, list[dict]]]:
    figures_by_id = {
        str(figure.get("figure_id") or ""): figure
        for figure in canonical.get("figures") or []
        if isinstance(figure, dict) and figure.get("figure_id")
    }
    images_by_id = {
        str(image.get("image_id") or ""): image
        for image in canonical.get("images") or []
        if isinstance(image, dict) and image.get("image_id")
    }
    by_reference: dict[str, list[dict]] = {}
    for figure in figures_by_id.values():
        for reference in figure.get("reference_ids") or []:
            by_reference.setdefault(str(reference), []).append(figure)
    return figures_by_id, images_by_id, by_reference


def figure_urls_and_captions(
    figure_ids: list[str],
    figures_by_id: dict[str, dict],
    images_by_id: dict[str, dict],
) -> tuple[list[str], dict[str, str]]:
    urls: list[str] = []
    captions: dict[str, str] = {}
    for figure_id in figure_ids:
        figure = figures_by_id.get(figure_id, {})
        caption = str(figure.get("caption_raw") or "").strip()
        for image_id in figure.get("image_ids") or []:
            image = images_by_id.get(str(image_id), {})
            url = str(image.get("url") or "").strip()
            if not url:
                continue
            if url not in urls:
                urls.append(url)
            captions[url] = caption or str(image.get("alt_raw") or "").strip()
    return urls, captions


def replace_figure_ids(value: str, repairs: list[dict[str, Any]]) -> str:
    text = str(value or "")
    for repair in repairs:
        source_id = str(repair.get("from_figure_id") or "").strip()
        target_id = str(repair.get("to_figure_id") or "").strip()
        if not source_id or not target_id or source_id == target_id:
            continue
        pattern = re.compile(
            rf"(?i)\b(?P<prefix>Fig(?:ure)?\.?\s*){re.escape(source_id)}"
            rf"(?!\d)(?!\.\d)"
        )
        text, _count = pattern.subn(
            lambda match: f"{match.group('prefix')}{target_id}",
            text,
            count=1,
        )
    return text


def strip_public_layout(value: object) -> str:
    text = str(value or "")
    text = _IMAGE_TAG_RE.sub(" ", text)
    text = _MARKDOWN_IMAGE_RE.sub(" ", text)
    text = re.sub(
        r"\\begin\{figure\}.*?\\end\{figure\}",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"^\s*##+\s*(?:Activity|Discuss|Project)\s*",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = _LAYOUT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def name_key(value: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    plain = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", "", plain.casefold())


def caption_person_surnames(value: object) -> set[str]:
    surnames: set[str] = set()
    for match in _PERSON_NAME_SEQUENCE_RE.finditer(str(value or "")):
        surname = name_key(match.group("surname"))
        if surname and surname not in _PERSON_SURNAME_STOPWORDS:
            surnames.add(surname)
    return surnames


def strict_display_repairs(
    task: dict[str, Any],
    repairs: list[dict[str, Any]],
    figures_by_reference: dict[str, list[dict]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    raw_key = name_key(task.get("raw_prompt") or "")
    for repair in repairs:
        target_id = str(repair.get("to_figure_id") or "").strip()
        matched = {
            name_key(value) for value in repair.get("matched_names") or []
            if name_key(value)
        }
        captions = [
            str(figure.get("caption_raw") or "")
            for figure in figures_by_reference.get(target_id, [])
        ]
        surnames = {
            surname for caption in captions
            for surname in caption_person_surnames(caption)
        }
        if matched and matched.issubset(surnames) and all(
            value in raw_key for value in matched
        ):
            accepted.append(copy.deepcopy(repair))
        else:
            rejected.append(copy.deepcopy(repair))
    return accepted, rejected


def normalize_visual_ownership(canonical: dict[str, Any]) -> int:
    """Separate raw and display Figure ownership and reject unsafe repairs."""
    figures_by_id, images_by_id, figures_by_reference = figure_maps(canonical)
    changed = 0
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        original_figure_refs = [
            str(value) for value in task.get("figure_refs") or [] if value
        ]
        original_image_urls = [
            str(value) for value in task.get("image_urls") or [] if value
        ]
        raw_prompt = strip_public_layout(task.get("raw_prompt") or "")
        raw_reference_ids = figure_reference_ids(raw_prompt)
        candidate_repairs = [
            copy.deepcopy(item) for item in task.get("display_overrides") or []
            if isinstance(item, dict)
        ]
        repairs, rejected_repairs = strict_display_repairs(
            task, candidate_repairs, figures_by_reference
        )
        task["display_overrides"] = repairs
        if rejected_repairs:
            task["rejected_display_overrides"] = rejected_repairs
        else:
            task.pop("rejected_display_overrides", None)
        display_prompt = replace_figure_ids(raw_prompt, repairs)
        display_reference_ids = figure_reference_ids(display_prompt)

        def resolve(
            reference_ids: list[str],
        ) -> tuple[list[str], list[str], list[str]]:
            resolved: list[str] = []
            unresolved: list[str] = []
            ambiguous: list[str] = []
            for reference_id in reference_ids:
                candidates = figures_by_reference.get(reference_id, [])
                primary = [
                    figure for figure in candidates
                    if (figure.get("reference_ids") or [""])[0] == reference_id
                ]
                if len(primary) == 1:
                    candidates = primary
                if len(candidates) == 1:
                    figure_id = str(candidates[0].get("figure_id") or "")
                    if figure_id and figure_id not in resolved:
                        resolved.append(figure_id)
                elif not candidates:
                    unresolved.append(reference_id)
                else:
                    ambiguous.append(reference_id)
            return resolved, unresolved, ambiguous

        raw_figure_ids, raw_unresolved, raw_ambiguous = resolve(raw_reference_ids)
        display_figure_ids, display_unresolved, display_ambiguous = resolve(
            display_reference_ids
        )
        if not display_reference_ids:
            compatible = []
            for figure_id in original_figure_refs:
                caption = str(
                    figures_by_id.get(figure_id, {}).get("caption_raw") or ""
                )
                if visuals_compatible(raw_prompt, caption):
                    compatible.append(figure_id)
            display_figure_ids = unique(compatible)
            raw_figure_ids = unique(compatible)

        raw_urls, raw_captions = figure_urls_and_captions(
            raw_figure_ids, figures_by_id, images_by_id
        )
        display_urls, display_captions = figure_urls_and_captions(
            display_figure_ids, figures_by_id, images_by_id
        )
        inline_urls = [
            match.group("src")
            for pattern in (_IMAGE_TAG_RE, _MARKDOWN_IMAGE_RE)
            for match in pattern.finditer(str(task.get("raw_prompt") or ""))
        ]
        if inline_urls and not raw_urls:
            raw_urls = unique(inline_urls)
        if inline_urls and not display_urls and not repairs:
            display_urls = unique(inline_urls)
        if display_figure_ids and not display_urls:
            display_urls = unique(original_image_urls)
        if raw_figure_ids and not raw_urls:
            raw_urls = unique(original_image_urls)

        tags: list[str] = []
        for index, url in enumerate(display_urls):
            caption = (
                display_captions.get(url)
                or raw_captions.get(url)
                or f"Source visual {index + 1}"
            )
            try:
                tags.append(kr.image(url, caption))
            except ValueError:
                continue
        display_prompt = kr.canonicalize_rich_text(display_prompt).strip()
        display_prompt = " ".join(
            part for part in (display_prompt, *tags) if part
        ).strip()

        task["raw_prompt"] = raw_prompt
        task["display_prompt"] = display_prompt
        task["raw_figure_reference_ids"] = raw_reference_ids
        task["display_figure_reference_ids"] = display_reference_ids
        task["raw_figure_refs"] = raw_figure_ids
        task["display_figure_refs"] = display_figure_ids
        task["raw_image_urls"] = raw_urls
        task["display_image_urls"] = display_urls
        task["raw_image_captions"] = raw_captions
        task["display_image_captions"] = display_captions
        task["figure_refs"] = display_figure_ids
        task["image_urls"] = display_urls
        task["_image_captions"] = display_captions
        task["explicit_figure_reference_ids"] = display_reference_ids
        task["unresolved_figure_reference_ids"] = display_unresolved
        task["ambiguous_figure_reference_ids"] = display_ambiguous
        task["raw_unresolved_figure_reference_ids"] = raw_unresolved
        task["raw_ambiguous_figure_reference_ids"] = raw_ambiguous
        task["requires_visual"] = bool(
            display_urls
            or display_reference_ids
            or (task.get("requires_visual") and display_figure_ids)
        )
        if (
            original_figure_refs != display_figure_ids
            or original_image_urls != display_urls
        ):
            changed += 1
    return changed


def tabular_context(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"\\begin\{tabular\}\{[^}]*\}", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\\end\{tabular\}", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\\hline", "", text, flags=re.IGNORECASE)
    rows: list[str] = []
    for fragment in re.split(r"\\\\(?:\s*\n)?", text):
        fragment = re.sub(r"\s+", " ", fragment).strip()
        if not fragment:
            continue
        cells = [
            re.sub(r"\s+", " ", cell).strip() for cell in fragment.split("&")
        ]
        rows.append(" | ".join(cells))
    return "\n".join(rows).strip()


def link_shared_context(canonical: dict[str, Any]) -> int:
    blocks = [
        block for block in canonical.get("blocks") or [] if isinstance(block, dict)
    ]
    linked = 0
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        prompt = str(task.get("raw_prompt") or "")
        if not _CONTEXT_CUE_RE.search(prompt) and not _BOX_REF_RE.search(prompt):
            continue
        task_start = int(task.get("source_start") or 0)
        candidates = [
            block for block in blocks
            if block.get("kind") == "table"
            and int(block.get("source_end") or 0) <= task_start
            and task_start - int(block.get("source_end") or 0) <= 6000
        ]
        if not candidates:
            continue
        block = max(candidates, key=lambda item: int(item.get("source_end") or 0))
        context = tabular_context(block.get("raw_text") or "")
        if not context:
            continue
        task["shared_context_block_ids"] = [str(block.get("block_id") or "")]
        task["shared_context"] = context
        task["requires_context"] = True
        content_objects = task.get("content_objects")
        if not isinstance(content_objects, dict):
            content_objects = {}
        content_objects["shared_context_blocks"] = [{
            "block_id": block.get("block_id"),
            "kind": block.get("kind"),
            "display_text": context,
        }]
        task["content_objects"] = content_objects
        linked += 1
    return linked


def orphan_figure_issues(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    owned_figures = {
        str(figure_id)
        for task in canonical.get("tasks") or []
        if isinstance(task, dict)
        for figure_id in (
            task.get("display_figure_refs") or task.get("figure_refs") or []
        )
    }
    figure_by_block = {
        str(figure.get("block_id") or ""): figure
        for figure in canonical.get("figures") or []
        if isinstance(figure, dict)
    }
    issues: list[dict[str, Any]] = []
    for block in canonical.get("blocks") or []:
        if not isinstance(block, dict) or block.get("kind") != "figure":
            continue
        figure = figure_by_block.get(str(block.get("block_id") or ""), {})
        figure_id = str(figure.get("figure_id") or block.get("figure_id") or "")
        source_task_ids = [
            value for value in block.get("source_task_ids") or [] if value
        ]
        current_task_ids = [value for value in block.get("task_ids") or [] if value]
        if (
            (source_task_ids or current_task_ids)
            and figure_id
            and figure_id not in owned_figures
        ):
            issues.append({
                "severity": "error",
                "code": "phase21_orphan_task_figure",
                "message": (
                    "A Figure was enclosed by a source task boundary but no "
                    "canonical task owns it after visual compatibility checks."
                ),
                "figure_id": figure_id,
                "block_id": block.get("block_id"),
                "caption": figure.get("caption_raw") or "",
                "source_task_ids": source_task_ids or current_task_ids,
            })
    return issues


def strict_named_figure_repair(generation, item: dict, layout: list[dict]) -> dict | None:
    """Repair only a unique person surname, never a subject such as Germania."""
    from . import source_visual_contract as svc

    raw = str(item.get("raw_task") or item.get("normalized_task") or "")
    prose = generation._strip_source_visual_markup(raw)
    prose = generation._public_task_without_latex_layout(prose)
    explicit = generation._figure_reference_ids(prose)
    if len(explicit) != 1:
        return None
    source_id = explicit[0]
    try:
        section_index = int(item.get("_source_section_index"))
    except (TypeError, ValueError):
        return None
    nearby = [
        figure for figure in layout
        if abs(int(figure.get("section_index", -999)) - section_index) <= 1
    ]
    if not nearby:
        return None
    task_tokens = svc._proper_name_tokens(prose)
    surnames_by_id: dict[str, set[str]] = {}
    entries_by_id: dict[str, list[dict]] = {}
    for figure in nearby:
        figure_id = str(figure.get("figure_id") or "").strip()
        if not figure_id:
            continue
        entries_by_id.setdefault(figure_id, []).append(figure)
        surnames_by_id.setdefault(figure_id, set()).update(
            caption_person_surnames(figure.get("caption") or "")
        )
    frequency = Counter(
        surname for surnames in surnames_by_id.values() for surname in surnames
    )
    unique_task_surnames = {
        token for token in task_tokens
        if frequency.get(token) == 1 and token not in _PERSON_SURNAME_STOPWORDS
    }
    if not unique_task_surnames:
        return None
    scores = {
        figure_id: len(surnames & unique_task_surnames)
        for figure_id, surnames in surnames_by_id.items()
    }
    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    if not ranked or ranked[0][1] < 1:
        return None
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if ranked[0][1] <= runner_up:
        return None
    target_id = ranked[0][0]
    if target_id == source_id or scores.get(source_id, 0) > 0:
        return None
    entries = entries_by_id.get(target_id) or []
    if not entries:
        return None
    return {
        "from_figure_id": source_id,
        "to_figure_id": target_id,
        "matched_names": sorted(
            surnames_by_id[target_id] & unique_task_surnames
        ),
        "entries": entries,
    }
