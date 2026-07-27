"""Deterministic Mathpix-MMD visual ownership and rich-text normalization.

Mathpix source files legitimately contain LaTeX layout scaffolding around figures
and tables. That scaffolding is useful while discovering source visuals, but it
must never be copied into teacher-facing Type Examples or Activity/Info Hubs.

The same source can also contain an internally inconsistent Figure number. A
repair is allowed only when one adjacent source caption is uniquely identified
by a proper name that appears in the task, while the explicitly numbered Figure
has no such name match. The raw source wording remains untouched; only the
teacher-facing display text and owned image metadata are corrected.
"""
from __future__ import annotations

import copy
import html
import re
import unicodedata
from functools import wraps
from types import ModuleType

_CONTRACT_VERSION = 1
_REPAIRS_KEY = "_source_visual_reference_repairs"
_ENABLED_KEY = "_source_visual_display_repair_enabled"

_LAYOUT_TOKEN_RE = re.compile(
    r"""\\(?:captionsetup|caption|includegraphics|begin\{(?:figure|table|tabular)\}
    |end\{(?:figure|table|tabular)\})""",
    re.IGNORECASE | re.VERBOSE,
)
_CANONICAL_IMAGE_RE = re.compile(
    r'\[img src="(?P<src>https://[^"]+)" alt="(?P<alt>[^"]+)"\]',
    re.IGNORECASE,
)
_PROPER_NAME_RE = re.compile(
    r"\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]{3,}\b"
)
_NAME_STOPWORDS = frozenset({
    "activity", "allegory", "allegorical", "artist", "box", "caption",
    "describe", "discuss", "explain", "figure", "fig", "historical",
    "identify", "image", "interpret", "look", "nation", "painting",
    "source", "the", "this", "what", "when", "where", "which", "with",
})


def _name_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    plain = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", "", plain.casefold())


def _proper_name_tokens(value: str) -> set[str]:
    tokens = {
        _name_key(match.group(0))
        for match in _PROPER_NAME_RE.finditer(str(value or ""))
    }
    return {
        token for token in tokens
        if len(token) >= 5 and token not in _NAME_STOPWORDS
    }


def _balanced_command_body(value: str, command: str) -> list[str]:
    """Return balanced braced bodies for one LaTeX command."""
    text = str(value or "")
    pattern = re.compile(
        rf"\\{re.escape(command)}(?:\[[^\]]*\])?\s*\{{",
        re.IGNORECASE,
    )
    bodies: list[str] = []
    cursor = 0
    while True:
        match = pattern.search(text, cursor)
        if match is None:
            break
        brace = text.find("{", match.start(), match.end())
        if brace < 0:
            break
        depth = 0
        index = brace
        end = None
        while index < len(text):
            character = text[index]
            if character == "\\":
                index += 2
                continue
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
            index += 1
        if end is None:
            break
        bodies.append(text[brace + 1:end - 1])
        cursor = end
    return bodies


def _raw_caption_map(generation: ModuleType, item: dict) -> dict[str, str]:
    """Source URL -> caption map, including truncated Mathpix figure blocks."""
    raw = str(
        item.get("raw_task")
        or item.get("normalized_task")
        or item.get("question")
        or ""
    )
    captions: dict[str, str] = {}

    source_caption_reader = getattr(generation, "_source_visual_captions", None)
    if callable(source_caption_reader):
        try:
            for url, caption in source_caption_reader(raw).items():
                if str(url or "").strip():
                    captions[str(url).strip()] = str(caption or "").strip()
        except Exception:
            pass

    for figure in generation._LATEX_FIGURE_BLOCK_RE.finditer(raw):
        body = figure.group("body") or ""
        include = generation._LATEX_INCLUDEGRAPHICS_RE.search(body)
        if include is None:
            continue
        url = str(include.group("url") or "").strip()
        caption_bodies = _balanced_command_body(body, "caption")
        caption = caption_bodies[-1] if caption_bodies else ""
        if url and caption:
            captions[url] = generation._clean_visual_caption(caption)

    # A task boundary can begin after ``\begin{figure}`` or end before
    # ``\end{figure}``. Pair one surviving includegraphics URL with the nearest
    # balanced caption instead of copying either layout command to public text.
    includes = list(generation._LATEX_INCLUDEGRAPHICS_RE.finditer(raw))
    caption_bodies = _balanced_command_body(raw, "caption")
    if len(includes) == 1 and caption_bodies:
        url = str(includes[0].group("url") or "").strip()
        captions.setdefault(
            url,
            generation._clean_visual_caption(caption_bodies[0]),
        )

    for match in _CANONICAL_IMAGE_RE.finditer(raw):
        captions.setdefault(
            match.group("src"),
            html.unescape(match.group("alt")),
        )
    return captions


def _canonical_owned_image_tags(
    generation: ModuleType,
    item: dict,
    rendered: str,
) -> list[str]:
    existing = {
        match.group("src"): html.unescape(match.group("alt"))
        for match in _CANONICAL_IMAGE_RE.finditer(str(rendered or ""))
    }
    captions = dict(existing)
    stored = item.get("_image_captions")
    if isinstance(stored, dict):
        captions.update({
            str(url): str(caption or "")
            for url, caption in stored.items()
            if str(url or "").strip()
        })
    captions.update({
        url: caption
        for url, caption in _raw_caption_map(generation, item).items()
        if caption
    })
    for repair in item.get(_REPAIRS_KEY) or []:
        if not isinstance(repair, dict):
            continue
        url = str(repair.get("url") or "").strip()
        caption = str(repair.get("caption") or "").strip()
        if url and caption:
            captions[url] = caption

    repair_urls: list[str] = []
    for repair in item.get(_REPAIRS_KEY) or []:
        if not isinstance(repair, dict):
            continue
        url = str(repair.get("url") or "").strip()
        if url and url not in repair_urls:
            repair_urls.append(url)

    urls: list[str] = list(repair_urls)
    if not urls:
        for url in item.get("image_urls") or []:
            url = str(url or "").strip()
            if url and url not in urls:
                urls.append(url)
    if not urls:
        urls.extend(existing)

    tags: list[str] = []
    plain_rendered = generation.kr._IMAGE_TAG_RE.sub(
        " ", str(rendered or "")
    )
    for image_index, url in enumerate(urls):
        caption = generation._clean_visual_caption(captions.get(url) or "")
        caption = generation._visual_alt_text(
            item,
            plain_rendered,
            image_index,
            caption,
        )
        try:
            tags.append(generation.kr.image(url, caption))
        except ValueError:
            continue
    return tags


def _replace_display_figure_references(value: str, repairs: list[dict]) -> str:
    text = str(value or "")
    for repair in repairs:
        if not isinstance(repair, dict):
            continue
        source_id = str(repair.get("from_figure_id") or "").strip()
        target_id = str(repair.get("to_figure_id") or "").strip()
        if not source_id or not target_id or source_id == target_id:
            continue
        pattern = re.compile(
            rf"(?i)\b(?P<prefix>Fig(?:ure)?\.?\s*)"
            rf"{re.escape(source_id)}(?!\d)(?!\.\d)"
        )
        text, _count = pattern.subn(
            lambda match: f"{match.group('prefix')}{target_id}",
            text,
            count=1,
        )
    return text


def _strip_residual_layout(generation: ModuleType, value: str) -> str:
    """Remove presentation scaffolding only, retaining task wording."""
    text = str(value or "")
    text = generation.kr._IMAGE_TAG_RE.sub(" ", text)
    text = generation._LATEX_FIGURE_BLOCK_RE.sub(" ", text)
    text = generation._strip_source_visual_markup(text)
    text = generation._public_task_without_latex_layout(text)

    # Remove balanced captions that survived an incomplete figure boundary.
    for command in ("caption", "captionsetup"):
        while True:
            bodies = _balanced_command_body(text, command)
            if not bodies:
                break
            pattern = re.compile(
                rf"\\{command}(?:\[[^\]]*\])?\s*\{{",
                re.IGNORECASE,
            )
            match = pattern.search(text)
            if match is None:
                break
            brace = text.find("{", match.start(), match.end())
            depth = 0
            index = brace
            end = None
            while index < len(text):
                character = text[index]
                if character == "\\":
                    index += 2
                    continue
                if character == "{":
                    depth += 1
                elif character == "}":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
                index += 1
            if end is None:
                break
            text = text[:match.start()] + " " + text[end:]

    text = re.sub(
        r"\\(?:begin|end)\{(?:figure|table|tabular)\}",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\\(?:captionsetup|includegraphics)\b[^\n]*",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def _render_inventory_display(
    generation: ModuleType,
    item: dict,
    rendered: str,
) -> str:
    tags = _canonical_owned_image_tags(generation, item, rendered)
    body = generation.kr._IMAGE_TAG_RE.sub(" ", str(rendered or ""))
    body = _replace_display_figure_references(
        body,
        list(item.get(_REPAIRS_KEY) or []),
    )
    if _LAYOUT_TOKEN_RE.search(body):
        body = _strip_residual_layout(generation, body)
    body = generation.kr.canonicalize_rich_text(body).strip()
    output = " ".join(part for part in (body, *tags) if part).strip()
    output = generation.kr.canonicalize_rich_text(output)

    issues = set(generation.kr.rich_text_issues(output))
    if issues and issues.issubset({"raw_latex", "raw_math_expression"}):
        repaired = generation.kr.repair_unwrapped_math(output)
        if (
            not generation.kr.rich_text_issues(repaired)
            and generation.kr.unwrap_katex(repaired)
            == generation.kr.unwrap_katex(output)
        ):
            output = repaired
    return output.strip()


def _nearby_unique_named_figure(
    generation: ModuleType,
    item: dict,
    layout: list[dict],
) -> dict | None:
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

    caption_names_by_id: dict[str, set[str]] = {}
    entries_by_id: dict[str, list[dict]] = {}
    for figure in nearby:
        figure_id = str(figure.get("figure_id") or "").strip()
        if not figure_id:
            continue
        entries_by_id.setdefault(figure_id, []).append(figure)
        caption_names_by_id.setdefault(figure_id, set()).update(
            _proper_name_tokens(figure.get("caption") or "")
        )
    frequency: dict[str, int] = {}
    for names in caption_names_by_id.values():
        for name in names:
            frequency[name] = frequency.get(name, 0) + 1

    task_names = _proper_name_tokens(prose)
    unique_task_names = {
        name for name in task_names if frequency.get(name) == 1
    }
    if not unique_task_names:
        return None

    scores = {
        figure_id: len(names & unique_task_names)
        for figure_id, names in caption_names_by_id.items()
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

    matched_names = sorted(
        caption_names_by_id[target_id] & unique_task_names
    )
    return {
        "from_figure_id": source_id,
        "to_figure_id": target_id,
        "matched_names": matched_names,
        "entries": entries,
    }


def _annotate_reference_repairs(
    generation: ModuleType,
    items: list[dict],
    sections: list[dict],
) -> list[dict]:
    layout = generation._source_figure_layout(sections)
    if not layout:
        return items

    out: list[dict] = []
    repaired_count = 0
    for raw_item in items:
        item = copy.deepcopy(raw_item)
        item.pop(_REPAIRS_KEY, None)
        repair = _nearby_unique_named_figure(generation, item, layout)
        if repair is None:
            out.append(item)
            continue

        entries = repair.pop("entries")
        urls: list[str] = []
        captions: dict[str, str] = {}
        for entry in entries:
            url = str(entry.get("url") or "").strip()
            if not url or url in urls:
                continue
            urls.append(url)
            captions[url] = generation._clean_visual_caption(
                entry.get("caption") or ""
            )
        if not urls:
            out.append(item)
            continue

        evidence = {
            **repair,
            "url": urls[0],
            "caption": captions.get(urls[0], ""),
            "reason": "unique_adjacent_caption_name_match",
        }
        # Preserve the raw inventory ownership exactly as extracted from the
        # MMD. The corrected URL/caption live only in display-repair metadata,
        # and are selected by ``_canonical_owned_image_tags``.
        item[_REPAIRS_KEY] = [evidence]
        out.append(item)
        repaired_count += 1

    if repaired_count:
        generation.progress.log(
            "Corrected "
            f"{repaired_count} internally inconsistent source Figure "
            "reference(s) for display using a unique adjacent caption-name "
            "match; raw MMD wording remains preserved.",
            level="warning",
        )
    return out


def _enable_inventory_display_repairs(inventory: dict | None) -> dict:
    """Activate display-only repairs after the source inventory is finalized."""
    value = inventory if isinstance(inventory, dict) else {}
    for item in value.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get(_REPAIRS_KEY):
            item[_ENABLED_KEY] = True
        else:
            item.pop(_ENABLED_KEY, None)
    return value


def install(generation: ModuleType) -> None:
    """Install source-visual normalization after the topology contracts."""
    if getattr(
        generation, "_SOURCE_VISUAL_CONTRACT_VERSION", 0
    ) >= _CONTRACT_VERSION:
        return

    original_attach = generation._attach_explicit_figure_images
    original_inventory_text = generation._inventory_task_text
    original_hub_note = generation._compact_activity_hub_note
    original_extract_inventory = (
        generation._extract_question_task_inventory_via_api
    )
    original_refresh_inventory = generation._refresh_inventory_from_source_anchors
    original_refresh_figures = generation._refresh_inventory_figure_metadata

    generation._SOURCE_VISUAL_ORIGINAL_ATTACH = original_attach
    generation._SOURCE_VISUAL_ORIGINAL_INVENTORY_TEXT = original_inventory_text
    generation._SOURCE_VISUAL_ORIGINAL_HUB_NOTE = original_hub_note
    generation._SOURCE_VISUAL_ORIGINAL_EXTRACT_INVENTORY = (
        original_extract_inventory
    )
    generation._SOURCE_VISUAL_ORIGINAL_REFRESH_INVENTORY = (
        original_refresh_inventory
    )
    generation._SOURCE_VISUAL_ORIGINAL_REFRESH_FIGURES = original_refresh_figures

    @wraps(original_attach)
    def attach_explicit_figure_images(
        items: list[dict], sections: list[dict]
    ) -> list[dict]:
        attached = original_attach(items, sections)
        return _annotate_reference_repairs(
            generation, attached, sections
        )

    @wraps(original_inventory_text)
    def inventory_task_text(item: dict) -> str:
        rendered = original_inventory_text(item)
        display_item = item
        if not item.get(_ENABLED_KEY):
            display_item = copy.deepcopy(item)
            display_item.pop(_REPAIRS_KEY, None)
        return _render_inventory_display(
            generation, display_item, rendered
        )

    @wraps(original_extract_inventory)
    def extract_question_task_inventory(*args, **kwargs):
        return _enable_inventory_display_repairs(
            original_extract_inventory(*args, **kwargs)
        )

    @wraps(original_refresh_inventory)
    def refresh_inventory_from_source_anchors(*args, **kwargs):
        return _enable_inventory_display_repairs(
            original_refresh_inventory(*args, **kwargs)
        )

    @wraps(original_refresh_figures)
    def refresh_inventory_figure_metadata(*args, **kwargs):
        return _enable_inventory_display_repairs(
            original_refresh_figures(*args, **kwargs)
        )

    @wraps(original_hub_note)
    def compact_activity_hub_note(item: dict) -> str:
        rendered = original_hub_note(item)
        if (
            _LAYOUT_TOKEN_RE.search(str(rendered or ""))
            or generation.kr.rich_text_issues(rendered)
            or (
                item.get(_ENABLED_KEY)
                and item.get(_REPAIRS_KEY)
            )
        ):
            # The exact inventory task is the authoritative public version. It
            # has source layout removed and carries only owned canonical images.
            rendered = inventory_task_text(item)
        else:
            rendered = _render_inventory_display(
                generation, item, rendered
            )
        if _LAYOUT_TOKEN_RE.search(rendered):
            raise RuntimeError(
                "Activity/Info Hub display still contains Mathpix layout "
                "commands after deterministic source normalization"
            )
        return rendered

    generation._attach_explicit_figure_images = attach_explicit_figure_images
    generation._inventory_task_text = inventory_task_text
    generation._extract_question_task_inventory_via_api = (
        extract_question_task_inventory
    )
    generation._refresh_inventory_from_source_anchors = (
        refresh_inventory_from_source_anchors
    )
    generation._refresh_inventory_figure_metadata = (
        refresh_inventory_figure_metadata
    )
    generation._compact_activity_hub_note = compact_activity_hub_note
    generation._enable_source_visual_display_repairs = (
        _enable_inventory_display_repairs
    )
    generation._SOURCE_VISUAL_CONTRACT_VERSION = _CONTRACT_VERSION
