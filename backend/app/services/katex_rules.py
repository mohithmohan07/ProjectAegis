"""KaTeX / rich-text content rules for assessment generation.

The Bulk Import workbook's rich-text columns accept a strict subset of
markup. These rules are the single source of truth shared by:
  - the synthetic dry-mode generator (emits sample content in this format)
  - the live (OpenAI) generation prompt (injected as a preamble)

Allowed in rich-text columns
  Plain text   - typed directly
  Equation     - [Katex] LaTeX [/Katex]   (inline/block auto-detected)
  Image        - [img src="https://..." alt="..."]
  Link         - [Display Text](https://...)

Declared ``Equation`` answer/keyword cells are NOT rich text: the CMS uses
their ``answer_type`` to render the whole cell, so they hold raw LaTeX with no
``[Katex]`` wrapper.  Declared ``Phrases`` cells are wholly plain text.  These
typed-cell rules deliberately live beside the general rich-text rules so the
author checker, renderer, release freeze, read-back, and importer cannot drift.
"""
from __future__ import annotations

import html
import re
from collections.abc import Callable
from urllib.parse import urlsplit

# Canonical Bulk Import field names that accept rich text.
RICH_TEXT_FIELDS = frozenset({
    "question", "answer", "display_answer", "answer_explanation",
    "concept_details",
})
# Type-declared answer and sub-question keyword fields; Equation values are
# raw LaTeX with no [Katex] wrapper.
RAW_KATEX_FIELDS = frozenset({"answer_content", "keyword"})

# Tokens whose presence switches KaTeX to block (display) mode.
_BLOCK_TRIGGERS = (r"\begin", r"\array", r"\frac", r"\sum", r"\int", r"\prod", r"\oint")


def katex(latex: str) -> str:
    return f"[Katex] {latex.strip()} [/Katex]"


def is_block(latex: str) -> bool:
    return any(tok in latex for tok in _BLOCK_TRIGGERS)


def _public_http_url(
    value: str, *, label: str, require_https: bool = False,
) -> str:
    value = str(value or "").strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or (require_https and parsed.scheme != "https")
        or not parsed.netloc
        or re.search(r"""[\s"'<>[\]]""", value)
    ):
        scheme = "https://" if require_https else "http(s)://"
        raise ValueError(f"{label} must be a full public {scheme} URL")
    return value


def image(src: str, alt: str, *, width: str | None = None, height: str | None = None) -> str:
    if width or height:
        raise ValueError(
            "canonical image tags support only src and alt attributes")
    src = _public_http_url(src, label="image src", require_https=True)
    safe_alt = html.escape(
        re.sub(r"\s+", " ", (alt or "").strip()), quote=True
    ).replace("[", "&#91;").replace("]", "&#93;")
    if not safe_alt:
        raise ValueError("image alt text is required")
    return f'[img src="{src}" alt="{safe_alt}"]'


def link(text: str, url: str) -> str:
    url = _public_http_url(url, label="link url")
    return f"[{text}]({url})"


def _tex_text(value: str) -> str:
    """Encode one already-plain segment as a lossless TeX text atom."""

    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "#": r"\#",
        "$": r"\$",
        "%": r"\%",
        "&": r"\&",
        "_": r"\_",
        "^": r"\^{}",
        "~": r"\~{}",
    }
    escaped = "".join(replacements.get(character, character) for character in value)
    return rf"\text{{{escaped}}}" if escaped else ""


def raw_equation_cell(content: str) -> str:
    """Return one type-declared Equation cell as full raw LaTeX.

    New author output is already raw and passes through byte-for-byte.  The
    only rewrite is a lossless compatibility repair for historic body-style
    cells that mixed prose with one or more ``[Katex]`` spans.  Each outside
    segment becomes an explicit ``\text{...}`` atom and each span contributes
    its exact LaTeX body.  This is serialization only: it neither interprets
    the equation nor chooses a different answer medium.
    """

    value = str(content or "")
    matches = list(_KATEX_TAG_RE.finditer(value))
    if not matches:
        if (
            not _KATEX_TOKEN_RE.search(value)
            and not _KATEX_LIKE_TAG_RE.search(value)
            and _equation_has_loose_prose(value)
        ):
            return _tex_text(value).strip()
        return value.strip()
    # Do not guess through malformed wrapper-shaped text.  Its checker/read-
    # back defect remains visible and blocks publication.
    if len(matches) * 2 != len(list(_KATEX_TOKEN_RE.finditer(value))):
        return value
    parts: list[str] = []
    cursor = 0
    for match in matches:
        parts.append(_tex_text(value[cursor:match.start()]))
        parts.append(str(match.group("body") or "").strip())
        cursor = match.end()
    parts.append(_tex_text(value[cursor:]))
    return "".join(part for part in parts if part).strip()


def raw_answer_cell(answer_type: str, content: str) -> str:
    """Render a type-declared answer/keyword cell on the CMS wire.

    The declared type is the only switch.  Equation cells become one full raw
    LaTeX expression with no ``[Katex]`` token; Image cells that consist of one
    canonical image tag reduce to its source URL; Phrases and unknown values
    pass through verbatim.  No content meaning is inferred.
    """
    text = replace_unsupported_tables(str(content or ""))
    kind = str(answer_type or "").strip().lower()
    if kind == "equation":
        return raw_equation_cell(text)
    if kind == "image":
        match = _CANONICAL_IMAGE_TAG_RE.search(text)
        if match and not _CANONICAL_IMAGE_TAG_RE.sub("", text).strip():
            return match.group("src")
    return text


def rich_answer_display(answer_type: str, content: str) -> str:
    """Project one typed cell when embedded in an untyped rich-text field."""

    text = replace_unsupported_tables(str(content or ""))
    if str(answer_type or "").strip().lower() == "equation":
        return katex(raw_equation_cell(text))
    return text


_KATEX_TAG_RE = re.compile(
    r"\[katex\]\s*(?P<body>.*?)\s*\[/katex\]",
    re.IGNORECASE | re.DOTALL,
)
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(\s*"
    r"(?P<src>https://(?:[^()\s]|(?:\([^()]*\)))+)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)",
    re.IGNORECASE,
)
_INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics(?:\[[^\]]*\])?\{(?P<src>https://[^}\s]+)\}",
    re.IGNORECASE,
)
_TABULAR_RE = re.compile(
    r"\\begin\s*\{\s*tabular\s*\}\s*"
    r"(?:\[[^\]]*\]\s*)?\{(?P<columns>[^}]*)\}"
    r"(?P<body>.*?)\\end\s*\{\s*tabular\s*\}",
    re.IGNORECASE | re.DOTALL,
)
_UNTERMINATED_TABULAR_RE = re.compile(
    r"\\begin\s*\{\s*tabular\s*\}\s*"
    r"(?:\[[^\]]*\]\s*)?\{(?P<columns>[^}]*)\}"
    r"(?P<body>.*)\Z",
    re.IGNORECASE | re.DOTALL,
)
_UNSUPPORTED_TABLE_BEGIN_RE = re.compile(
    r"\\begin\s*\{\s*tabular\s*\}", re.IGNORECASE,
)
_ARRAY_BEGIN_RE = re.compile(
    r"\\begin\s*\{\s*array\s*\}", re.IGNORECASE,
)
_ARRAY_END_RE = re.compile(
    r"\\end\s*\{\s*array\s*\}", re.IGNORECASE,
)
_CANONICAL_ARRAY_ENV_RE = re.compile(
    r"\\begin\{array\}\{[^{}\r\n]+\}"
    r"(?P<body>.*?)\\end\{array\}",
    re.DOTALL,
)
_UNSUPPORTED_KATEX_COMMAND_RE = re.compile(
    r"\\(?:mathrm|hspace|phantom|boxed)\b", re.IGNORECASE,
)
# TeX permits an optional vertical-space argument immediately after a row
# break (for example ``\\[0.4cm]``).  That form is not in the CMS KaTeX
# subset: authors must use an ordinary ``\\`` row break instead.
_RAW_ROW_SPACING_RE = re.compile(
    r"(?<!\\)\\\\\s*\[[^\]\r\n]*\]",
)
_LITERAL_NEWLINE_BEFORE_LIST_ITEM_RE = re.compile(
    r"\\n(?=[ \t]*(?:\([A-Za-z]\)|[A-Za-z][.)]"
    r"|\([ivxlcdmIVXLCDM]+\)|\d+[.)]|[-*•]))",
)
_LEGACY_ROMAN_ATOM_RE = re.compile(
    r"\\mathrm\s*\{",
)
# Only a dimension-shaped optional row gap can be removed without risking
# that a bracketed array cell (for example ``[1, 2]``) is mistaken for
# presentation-only spacing.
_LEGACY_SAFE_ROW_SPACING_RE = re.compile(
    r"(?<!\\)\\\\\s*\[\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*"
    r"(?:pt|pc|in|bp|cm|mm|dd|cc|sp|ex|em)\s*\]",
    re.IGNORECASE,
)
_TABLE_ROW_RE = re.compile(r"(?<!\\)\\\\(?:\[[^\]]*\])?")
_TABLE_COLUMN_RE = re.compile(r"(?<!\\)&")
_TABLE_RULE_RE = re.compile(
    r"\\(?:hline|toprule|midrule|bottomrule)\b"
    r"|\\cline\{[^}]*\}",
    re.IGNORECASE,
)
_FOOTNOTE_RE = re.compile(
    r"\\footnotetext\{(?P<body>[^{}]*(?:\{[^{}]*\}[^{}]*)*)\}",
    re.IGNORECASE | re.DOTALL,
)
_RAW_BLOCK_MATH_PATTERNS = (
    re.compile(
        r"(?<!\\)\$\$(?P<body>.+?)(?<!\\)\$\$", re.DOTALL,
    ),
    re.compile(
        r"(?<!\\)\\\[(?P<body>.+?)(?<!\\)\\\]", re.DOTALL,
    ),
    re.compile(
        r"(?<!\\)\\\((?P<body>.+?)(?<!\\)\\\)", re.DOTALL,
    ),
)
_SINGLE_DOLLAR_MATH_RE = re.compile(
    r"(?<!\\)\$(?!\$)(?P<body>[^$\n]+?)(?<!\\)\$(?!\$)"
)
_RAW_MATH_PATTERNS = (*_RAW_BLOCK_MATH_PATTERNS, _SINGLE_DOLLAR_MATH_RE)
_KATEX_TOKEN_RE = re.compile(r"\[(?P<close>/)?katex\]", re.IGNORECASE)
_KATEX_LIKE_TAG_RE = re.compile(r"\[/?katex\b[^\]]*\]", re.IGNORECASE)
_UPPERCASE_OBJECTIVE_OPTION_RE = re.compile(
    r"(?m)^[ \t]*(?P<label>[A-Z])\)"
)


def uppercase_objective_option_labels(
    text: str, option_count: int,
) -> tuple[str, ...]:
    """Return expected objective option labels authored in uppercase.

    Only labels at the start of a line are option syntax.  The populated
    answer-block count bounds the expected alphabet, so prose such as
    ``"Answer A) is discussed"`` and unrelated section labels do not become
    semantic guesses.
    """

    count = max(0, min(int(option_count or 0), 26))
    expected = {
        chr(ord("A") + index) for index in range(count)
    }
    found: list[str] = []
    for match in _UPPERCASE_OBJECTIVE_OPTION_RE.finditer(str(text or "")):
        label = str(match.group("label") or "")
        rendered = f"{label})"
        if label in expected and rendered not in found:
            found.append(rendered)
    return tuple(found)


def lowercase_objective_option_labels(
    text: str, option_count: int,
) -> str:
    """Lowercase only line-leading labels in the declared option range.

    This is the export-side companion to
    :func:`uppercase_objective_option_labels`.  It uses the same anchored
    syntax and option-capacity bound, so prose such as ``"Answer A)"`` and a
    later section label outside the workbook's option range stay byte-exact.
    """

    value = str(text or "")
    count = max(0, min(int(option_count or 0), 26))
    expected = {
        chr(ord("A") + index) for index in range(count)
    }

    def replace(match: re.Match) -> str:
        label = str(match.group("label") or "")
        if label not in expected:
            return match.group(0)
        rendered = match.group(0)
        offset = match.start("label") - match.start()
        return rendered[:offset] + label.lower() + rendered[offset + 1:]

    return _UPPERCASE_OBJECTIVE_OPTION_RE.sub(replace, value)


_IMAGE_TAG_RE = re.compile(r"\[img\b[^\]]*\]", re.IGNORECASE)
_CANONICAL_IMAGE_TAG_RE = re.compile(
    r'\[img src="(?P<src>https://[^"]+)" alt="(?P<alt>[^"]+)"\]'
)
_MARKDOWN_LINK_RE = re.compile(
    r"\[[^\]]+\]\(https?://(?:[^()\s]|(?:\([^()]*\)))+\)",
    re.IGNORECASE,
)
_MARKDOWN_LINK_CAPTURE_RE = re.compile(
    r"\[(?P<text>[^\]]+)\]\("
    r"(?P<url>https?://(?:[^()\s]|(?:\([^()]*\)))+)\)",
    re.IGNORECASE,
)
_CURRENCY_TOKEN_RE = re.compile(
    r"(?<!\\)\$(?P<amount>\d+(?:[.,]\d+)?)\b"
)
_RAW_EQUATION_RE = re.compile(
    r"(?<![\w])"
    r"(?P<left>(?:[A-Za-z][A-Za-z0-9]*|\d+(?:\.\d+)?)"
    r"(?:\s*[+\-*/×÷]\s*"
    r"(?:[A-Za-z][A-Za-z0-9]*|\d+(?:\.\d+)?))*)"
    r"\s*(?<![=<>])=(?!=)\s*"
    r"(?P<right>(?:[A-Za-z][A-Za-z0-9]*|\d+(?:\.\d+)?)"
    r"(?:\s*[+\-*/×÷]\s*"
    r"(?:[A-Za-z][A-Za-z0-9]*|\d+(?:\.\d+)?))*)"
    r"(?![\w])"
)
_BARE_URL_RE = re.compile(r"https?://[^\s<>\[\]]+", re.IGNORECASE)
_RAW_LATEX_RE = re.compile(
    r"\\(?:[A-Za-z]+|[%_#{}])"
    r"|(?<![\w])(?:[A-Za-z0-9)}]+)\s*[_^]\s*"
    r"(?:\{[^}]*\}|[A-Za-z0-9])",
    re.IGNORECASE,
)
_RAW_SCRIPT_TAIL_RE = re.compile(
    r"(?<![\w])[_^]\s*"
    r"(?:\{[^}]*\}|\([^)]*\)|\[[^\]]*\]|[A-Za-z0-9])",
)


def _legacy_markup_as_text(content: str) -> str:
    """Flatten non-math rich markup while retaining any KaTeX spans."""

    value = str(content or "")
    value = _MARKDOWN_IMAGE_RE.sub(
        lambda match: (
            f"(Image: {(match.group('alt') or '').strip() or 'Source visual'})"
        ),
        value,
    )
    value = _CANONICAL_IMAGE_TAG_RE.sub(
        lambda match: (
            f"(Image: {(match.group('alt') or '').strip() or 'Source visual'})"
        ),
        value,
    )
    value = _IMAGE_TAG_RE.sub("(Image)", value)
    value = _MARKDOWN_LINK_CAPTURE_RE.sub(
        lambda match: str(match.group("text") or "").strip(), value,
    )
    value = _BARE_URL_RE.sub("(source link)", value)
    return value


def _strip_unmatched_katex_tokens(content: str) -> str:
    """Remove wrapper tokens that cannot belong to a balanced KaTeX span.

    Historical prose occasionally inherited a concept keyword consisting of
    the literal token ``[katex]``.  Treating that orphan as mathematics would
    turn an otherwise plain Phrases answer into an invalid Equation cell.
    Pair openings and closings with a stack, preserve every token belonging to
    a balanced span byte-for-byte, and remove only the unmatched tokens.
    """

    value = str(content or "")
    tokens = list(_KATEX_TOKEN_RE.finditer(value))
    if not tokens:
        return value

    opening_stack: list[int] = []
    paired: set[int] = set()
    for index, token in enumerate(tokens):
        if token.group("close"):
            if opening_stack:
                opening = opening_stack.pop()
                paired.update((opening, index))
        else:
            opening_stack.append(index)

    unmatched = [
        token for index, token in enumerate(tokens) if index not in paired
    ]
    if not unmatched:
        return value

    parts: list[str] = []
    cursor = 0
    for token in unmatched:
        parts.append(value[cursor:token.start()])
        cursor = token.end()
    parts.append(value[cursor:])
    # Removing an inline token can leave two horizontal separators between
    # the surrounding words.  Coalesce only that word-internal gap; retain
    # every newline (and indentation beside it) unchanged.
    return re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", "".join(parts))


def legacy_export_answer_cell(
    answer_type: str, content: str,
) -> tuple[str, str]:
    """Serialize one stored legacy cell into a Q21-compliant workbook copy.

    Stored rows are immutable historical input. Most cells already conform
    and follow ``raw_answer_cell`` unchanged. A historic Phrases cell may,
    however, contain rich math or link/image markup from the pre-Q21 writer.
    At this export-only migration seam, non-math markup becomes readable text;
    a cell carrying math syntax becomes one full Equation value, with prose
    encoded by ``raw_equation_cell``. The conversion is lexical and never
    changes the stored dictionary or interprets its subject matter.
    """

    declared = str(answer_type or "").strip()
    source = legacy_export_rich_text(str(content or ""))
    rendered = raw_answer_cell(declared, source)
    issues = answer_cell_issues(declared, rendered)
    if not issues or declared.casefold() != "phrases":
        return declared, rendered

    plain_markup = _legacy_markup_as_text(source)
    without_orphan_wrappers = _strip_unmatched_katex_tokens(plain_markup)
    if without_orphan_wrappers != plain_markup:
        phrase = raw_answer_cell("Phrases", without_orphan_wrappers)
        if not answer_cell_issues("Phrases", phrase):
            return "Phrases", phrase
        plain_markup = without_orphan_wrappers
    math_issues = {
        "phrases_katex", "phrases_latex", "phrases_math_delimiter",
    }
    if math_issues.intersection(issues):
        equation_source = canonicalize_rich_text(plain_markup)
        equation = raw_answer_cell("Equation", equation_source)
        equation_issues = answer_cell_issues("Equation", equation)
        if not equation_issues:
            return "Equation", equation
        # Historic formulae sometimes use multi-letter variable names such
        # as ``PT`` inside an otherwise valid KaTeX span.  The strict lexical
        # checker cannot distinguish those from loose prose.  Preserve the
        # entire authored value as an explicit TeX text atom rather than
        # guessing a variable split or letting the invalid legacy cell ship.
        # This compatibility fallback is safe only for that one lexical
        # defect.  Unsupported commands, delimiters, or malformed structures
        # must remain visible defects; escaping those as literal text would
        # silently change their mathematical meaning.
        if set(equation_issues) == {"equation_plain_text"}:
            literal_equation = _tex_text(unwrap_katex(plain_markup).strip())
            if not answer_cell_issues("Equation", literal_equation):
                return "Equation", literal_equation

    phrase = raw_answer_cell("Phrases", plain_markup)
    if not answer_cell_issues("Phrases", phrase):
        return "Phrases", phrase
    return declared, rendered


_TEX_COMMAND_GROUPS = {
    "frac": 2,
    "dfrac": 2,
    "tfrac": 2,
    "binom": 2,
    "sqrt": 1,
    "text": 1,
    "mathrm": 1,
    "mathbf": 1,
    "mathit": 1,
    "operatorname": 1,
    "overline": 1,
    "underline": 1,
    "bar": 1,
    "vec": 1,
    "hat": 1,
}


def _markdown_block_code_ranges(value: str) -> list[tuple[int, int]]:
    """Return fenced and indented Markdown code ranges."""

    ranges: list[tuple[int, int]] = []
    offset = 0
    fence_start: int | None = None
    fence_char = ""
    fence_length = 0
    indented_code = False
    previous_blank = True
    for line in value.splitlines(keepends=True):
        logical = line.rstrip("\r\n")
        if fence_start is not None:
            closing = re.fullmatch(
                rf" {{0,3}}{re.escape(fence_char)}"
                rf"{{{fence_length},}}[ \t]*",
                logical,
            )
            if closing:
                ranges.append((fence_start, offset + len(line)))
                fence_start = None
                fence_char = ""
                fence_length = 0
        else:
            is_blank = not logical.strip()
            is_indented = line.startswith("    ") or line.startswith("\t")
            if indented_code:
                if is_indented:
                    ranges.append((offset, offset + len(line)))
                    offset += len(line)
                    previous_blank = False
                    continue
                if is_blank:
                    offset += len(line)
                    previous_blank = True
                    continue
                indented_code = False
            opening = re.match(r"^ {0,3}(?P<fence>`{3,}|~{3,})", logical)
            if opening:
                marker = str(opening.group("fence") or "")
                info = logical[opening.end():]
                if marker[0] != "`" or "`" not in info:
                    fence_start = offset
                    fence_char = marker[0]
                    fence_length = len(marker)
            elif is_indented and previous_blank:
                ranges.append((offset, offset + len(line)))
                indented_code = True
            previous_blank = is_blank
        offset += len(line)
    if fence_start is not None:
        ranges.append((fence_start, len(value)))
    return ranges


def _markdown_inline_code_ranges(
    value: str,
    *,
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    """Return balanced variable-length backtick spans in one text gap."""

    ranges: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        opening = value.find("`", cursor, end)
        if opening < 0:
            break
        opening_end = opening + 1
        while opening_end < end and value[opening_end] == "`":
            opening_end += 1
        delimiter_length = opening_end - opening
        search = opening_end
        closing_end = -1
        while search < end:
            closing = value.find("`", search, end)
            if closing < 0:
                break
            run_end = closing + 1
            while run_end < end and value[run_end] == "`":
                run_end += 1
            if run_end - closing == delimiter_length:
                closing_end = run_end
                break
            search = run_end
        if closing_end < 0:
            cursor = opening_end
            continue
        ranges.append((opening, closing_end))
        cursor = closing_end
    return ranges


def _markdown_code_ranges(value: str) -> list[tuple[int, int]]:
    block_ranges = _markdown_block_code_ranges(value)
    inline_ranges: list[tuple[int, int]] = []
    cursor = 0
    for start, end in block_ranges:
        if cursor < start:
            inline_ranges.extend(_markdown_inline_code_ranges(
                value,
                start=cursor,
                end=start,
            ))
        cursor = max(cursor, end)
    if cursor < len(value):
        inline_ranges.extend(_markdown_inline_code_ranges(
            value,
            start=cursor,
            end=len(value),
        ))
    merged: list[tuple[int, int]] = []
    for start, end in sorted([*block_ranges, *inline_ranges]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def transform_outside_markdown_code(
    value: str,
    transform: Callable[[str], str],
) -> str:
    """Transform prose while preserving every Markdown code range exactly."""

    text = str(value or "")
    pieces: list[str] = []
    cursor = 0
    for start, end in _markdown_code_ranges(text):
        pieces.append(transform(text[cursor:start]))
        pieces.append(text[start:end])
        cursor = end
    pieces.append(transform(text[cursor:]))
    return "".join(pieces)


def _replace_markdown_code(
    value: str,
    replacement: Callable[[str], str],
) -> str:
    text = str(value or "")
    pieces: list[str] = []
    cursor = 0
    for start, end in _markdown_code_ranges(text):
        pieces.append(text[cursor:start])
        pieces.append(replacement(text[start:end]))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


_TEX_NOARG_COMMANDS = frozenset({
    "alpha", "beta", "gamma", "delta", "epsilon", "theta", "lambda",
    "mu", "nu", "pi", "rho", "sigma", "tau", "phi", "chi", "psi",
    "omega", "ldots", "cdots", "dots", "times", "div", "cdot", "pm",
    "mp", "le", "leq", "ge", "geq", "ne", "neq", "approx", "infty",
    "quad", "qquad",
})
_TEX_OPERATOR_COMMANDS = frozenset({
    "times", "div", "cdot", "pm", "mp", "le", "leq", "ge", "geq",
    "ne", "neq", "approx", "ldots", "cdots", "dots",
})
_MATH_OPERATOR_CHARS = frozenset("+-*/=<>(),[]\u00d7\u00f7")


def _plain_table_cell(value: str) -> str:
    """Expose one table cell without interpreting its subject matter."""

    cell = _TABLE_RULE_RE.sub("", str(value or "")).strip()
    cell = re.sub(r"\\displaystyle\b", "", cell).strip()
    # ``\text{...}`` and the related style groups carry literal cell labels.
    # Peel only balanced, innermost groups; every character inside survives.
    group = re.compile(
        r"\\(?:text|textrm|textsf|texttt|mathrm|mathbf|mathit)\{([^{}]*)\}"
    )
    while True:
        unwrapped = group.sub(lambda match: match.group(1), cell)
        if unwrapped == cell:
            break
        cell = unwrapped
    for pattern in _RAW_MATH_PATTERNS:
        cell = pattern.sub(lambda match: str(match.group("body") or ""), cell)
    cell = cell.replace("~", " ")
    cell = re.sub(r"\s+", " ", cell).strip()
    return cell or "(blank)"


def _labeled_table(body: str) -> str:
    """Project table structure to explicit row/column-labelled plain text."""

    raw_rows = _TABLE_ROW_RE.split(str(body or ""))
    # Some source converters emit a tabular declaration followed by newline
    # rows and literal pipes instead of TeX ``\\``/``&`` separators.  The
    # declaration still proves the structure; retain those rows mechanically.
    if len(raw_rows) == 1 and "\n" in raw_rows[0]:
        raw_rows = raw_rows[0].splitlines()
    if raw_rows and not _TABLE_RULE_RE.sub("", raw_rows[-1]).strip():
        raw_rows.pop()
    rows: list[str] = []
    row_number = 0
    for raw_row in raw_rows:
        cleaned_row = _TABLE_RULE_RE.sub("", raw_row).strip()
        # A rule-only fragment is layout, not an authored data row.
        if not cleaned_row and "&" not in raw_row:
            continue
        row_number += 1
        cells = _TABLE_COLUMN_RE.split(cleaned_row)
        if len(cells) == 1 and re.search(r"(?<!\\)\|", cleaned_row):
            cells = re.split(r"(?<!\\)\|", cleaned_row)
        rows.append("; ".join(
            f"Table row {row_number}, column {column_number}: "
            f"{_plain_table_cell(cell)}"
            for column_number, cell in enumerate(cells, start=1)
        ))
    return "\n".join(rows) or "Table row 1, column 1: (blank)"


def _markdown_table_cells(line: str) -> list[str]:
    """Split one pipe row while retaining escaped literal pipes."""

    cells = re.split(r"(?<!\\)\|", str(line or ""))
    if cells and not cells[0].strip():
        cells.pop(0)
    if cells and not cells[-1].strip():
        cells.pop()
    return [cell.replace(r"\|", "|") for cell in cells]


def _markdown_separator(line: str) -> bool:
    cells = _markdown_table_cells(line)
    return len(cells) >= 2 and all(
        re.fullmatch(r"\s*:?-{3,}:?\s*", cell) is not None
        for cell in cells
    )


def _markdown_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    return (
        len(_markdown_table_cells(lines[index])) >= 2
        and _markdown_separator(lines[index + 1])
    )


def _replace_markdown_tables(value: str) -> str:
    lines = str(value or "").splitlines()
    if not any(
        _markdown_table_start(lines, index) for index in range(len(lines))
    ):
        return str(value or "")
    output: list[str] = []
    index = 0
    while index < len(lines):
        if not _markdown_table_start(lines, index):
            output.append(lines[index])
            index += 1
            continue
        table_rows = [lines[index]]
        index += 2  # separator syntax is layout, never a data cell
        while index < len(lines) and len(
            _markdown_table_cells(lines[index])
        ) >= 2:
            table_rows.append(lines[index])
            index += 1
        for row_number, row in enumerate(table_rows, start=1):
            output.append("; ".join(
                f"Table row {row_number}, column {column_number}: "
                f"{_plain_table_cell(cell)}"
                for column_number, cell in enumerate(
                    _markdown_table_cells(row), start=1
                )
            ))
    return "\n".join(output)


def _has_markdown_table(value: str) -> bool:
    lines = str(value or "").splitlines()
    return any(_markdown_table_start(lines, index) for index in range(len(lines)))


def _has_noncanonical_array(value: str) -> bool:
    """Return whether array-like markup is not one complete CMS form.

    The supported spelling is deliberately exact:
    ``\\begin{array}{<columns>}...\\end{array}``.  Optional positioning,
    whitespace inside either environment token, missing column declarations,
    unterminated environments, and nested arrays stay visible as defects
    instead of being guessed into a different representation.
    """

    text = str(value or "")
    beginnings = list(_ARRAY_BEGIN_RE.finditer(text))
    endings = list(_ARRAY_END_RE.finditer(text))
    if not beginnings and not endings:
        return False
    canonical = list(_CANONICAL_ARRAY_ENV_RE.finditer(text))
    return (
        len(beginnings) != len(canonical)
        or len(endings) != len(canonical)
    )


def replace_unsupported_tables(text: str) -> str:
    """Replace unsupported tabular/Markdown markup without dropping a cell.

    A source-associated image is a semantic choice and therefore belongs to
    the model pass.  This fallback handles only the mechanical case in which
    an unsupported table dialect reaches a serializer: it retains ordered
    cells and labels their coordinates, with no inferred headings or
    reconstructed meaning.  Canonical KaTeX ``array`` environments are a
    supported CMS representation and pass through byte-for-byte.
    """

    value = str(text or "")

    def table(match: re.Match) -> str:
        return _labeled_table(str(match.group("body") or ""))

    def replace_dialects(body: str) -> str:
        replaced = _TABULAR_RE.sub(table, body)
        # Closed environments were consumed above.  A remaining declaration
        # is a malformed source-converter tail; label everything after it so
        # no cell or following text disappears and no unsupported dialect
        # reaches the CMS.
        replaced = _UNTERMINATED_TABULAR_RE.sub(table, replaced)
        return replaced

    def wrapped(match: re.Match) -> str:
        body = str(match.group("body") or "")
        replaced = replace_dialects(body)
        return replaced if replaced != body else match.group(0)

    # A tabular environment inside a KaTeX wrapper must lose the wrapper as
    # well: the CMS does not render that dialect through KaTeX.
    value = _KATEX_TAG_RE.sub(wrapped, value)
    # The same is true for raw display-math wrappers.  Removing only tabular
    # would otherwise let canonicalization re-wrap coordinate labels as
    # ``[Katex] Table row ... [/Katex]``.
    for pattern in _RAW_BLOCK_MATH_PATTERNS:
        value = pattern.sub(wrapped, value)
    value = replace_dialects(value)
    value = _replace_markdown_tables(value)
    return value


def _looks_like_currency_pair(match: re.Match) -> bool:
    """Avoid interpreting ``$5 to $10`` as one inline equation."""
    body = (match.group("body") or "").strip()
    if not body or not body[0].isdigit():
        return False
    after = match.string[match.end():]
    return bool(re.match(r"\s*\d", after))


def _has_raw_math(value: str) -> bool:
    if any(pattern.search(value) for pattern in _RAW_BLOCK_MATH_PATTERNS):
        return True
    return any(
        not _looks_like_currency_pair(match)
        for match in _SINGLE_DOLLAR_MATH_RE.finditer(value)
    )


_RAW_TEX_MATH_DELIMITER_RE = re.compile(
    r"(?<!\\)(?:\\\\)*\\[\[\]()]",
)
_UNESCAPED_DOLLAR_RE = re.compile(
    r"(?<!\\)(?:\\\\)*\$",
)


def _has_raw_tex_math_delimiter(value: str) -> bool:
    """Recognize TeX delimiters using the preceding backslash parity.

    A canonical array row break followed by a parenthesized cell contains
    ``\\\\(``, whose two backslashes are structural and do not open raw math.
    An odd run still leaves a real ``\\(``/``\\[`` delimiter after any escaped
    pairs and must be rejected.
    """

    return _RAW_TEX_MATH_DELIMITER_RE.search(str(value or "")) is not None


def _has_unescaped_dollar(value: str) -> bool:
    """Return whether a dollar is preceded by an even backslash run."""

    return _UNESCAPED_DOLLAR_RE.search(str(value or "")) is not None


def _has_raw_equation(value: str) -> bool:
    for match in _RAW_EQUATION_RE.finditer(value):
        left = re.sub(r"\s+", "", match.group("left"))
        right = re.sub(r"\s+", "", match.group("right"))
        if (
            re.search(r"[+\-*/×÷]", left + right)
            or re.fullmatch(r"[A-Za-z]", left)
            or re.fullmatch(r"\d+(?:\.\d+)?", left)
        ):
            return True
    return False


def unwrap_katex(text: str) -> str:
    """Remove valid KaTeX wrappers while preserving their exact expression."""
    return _KATEX_TAG_RE.sub(
        lambda match: (match.group("body") or "").strip(),
        str(text or ""),
    )


def _balanced_group_end(
    value: str, start: int, opening: str, closing: str,
) -> int | None:
    """Return the exclusive end of one balanced TeX argument group."""
    if start >= len(value) or value[start] != opening:
        return None
    depth = 0
    index = start
    while index < len(value):
        character = value[index]
        if character == "\\":
            index += 2
            continue
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _normalize_legacy_roman_atoms(value: str) -> str:
    """Rewrite plain, balanced ``\\mathrm{...}`` as supported ``\\text``.

    For a body made only of letters/digits and horizontal whitespace, both
    commands render the same upright text atom.  TeX is case-sensitive, and a
    body containing operators, commands, grouping, or other syntax may have
    mathematical semantics that ``\\text`` would change.  Those forms remain
    byte-for-byte visible so the strict validator refuses them instead of an
    export migration guessing their meaning.
    """

    parts: list[str] = []
    cursor = 0
    while True:
        match = _LEGACY_ROMAN_ATOM_RE.search(value, cursor)
        if match is None:
            parts.append(value[cursor:])
            break
        opening = value.find("{", match.start(), match.end())
        end = _balanced_group_end(value, opening, "{", "}")
        if end is None:
            parts.append(value[cursor:match.end()])
            cursor = match.end()
            continue
        body = value[opening + 1:end - 1]
        # Mathpix commonly uses a leading ``~`` inside ``\mathrm`` solely as
        # unit spacing (``\mathrm{~kg}``).  A normal space in ``\text`` has
        # the same displayed role and carries no mathematical operator.
        normalized_body = body.replace("~", " ")
        if not normalized_body.strip() or any(
            not (character.isalnum() or character in " \t")
            for character in normalized_body
        ):
            parts.append(value[cursor:end])
            cursor = end
            continue
        parts.append(value[cursor:match.start()])
        parts.append(r"\text{" + normalized_body + "}")
        cursor = end
    return "".join(parts)


def normalize_supported_text_atoms(value: str) -> str:
    """Deterministically rewrite plain ``\\mathrm{...}`` atoms as ``\\text``.

    The public seam for INGESTION-time canonicalization: model transcription
    (GPT PDF reading, like Mathpix before it) writes physics units in LaTeX
    house style (``\\mathrm{V}``), which the CMS KaTeX subset bans. Plain
    letter/digit/space bodies render identically as ``\\text`` atoms, so the
    rewrite is meaning-preserving; any other body is left byte-for-byte for
    the strict validator and source review to judge.
    """

    return _normalize_legacy_roman_atoms(str(value or ""))


def legacy_export_rich_text(text: str) -> str:
    """Project known historical rich text onto the current strict wire.

    This helper belongs only at public workbook serialization seams.  It
    performs deterministic, meaning-preserving repairs on the exported copy:
    unsupported table dialects are labelled by coordinates, plain balanced
    ``\\mathrm`` atoms become ``\\text`` atoms, presentation-only row gaps
    become ordinary row breaks, and a complete raw canonical array gains its
    required ``[Katex]`` wrapper.  A literal ``\\n`` immediately before a list
    label becomes the real line break it represented.  Other forbidden
    commands, malformed groups, and ambiguous row-spacing syntax remain
    untouched and therefore continue to fail strict validation.
    """

    value = replace_unsupported_tables(str(text or ""))
    value = _normalize_legacy_roman_atoms(value)
    value = _LEGACY_SAFE_ROW_SPACING_RE.sub(lambda _match: r"\\", value)
    value = _LITERAL_NEWLINE_BEFORE_LIST_ITEM_RE.sub(
        lambda _match: "\n", value,
    )
    stripped = value.strip()
    if _CANONICAL_ARRAY_ENV_RE.fullmatch(stripped):
        return katex(stripped)
    return value


_TEX_TEXT_COMMAND_RE = re.compile(
    r"\\(?:text|textrm|textsf|texttt|mathrm|mathbf|mathit|operatorname)\s*\{",
    re.IGNORECASE,
)


def _mask_tex_text_groups(value: str) -> str:
    """Mask balanced TeX groups whose contents are explicitly text-like."""

    masked = list(value)
    cursor = 0
    while True:
        match = _TEX_TEXT_COMMAND_RE.search(value, cursor)
        if match is None:
            break
        opening = value.find("{", match.start(), match.end())
        end = _balanced_group_end(value, opening, "{", "}")
        if end is None:
            cursor = match.end()
            continue
        masked[match.start():end] = " " * (end - match.start())
        cursor = end
    return "".join(masked)


def _equation_has_loose_prose(value: str) -> bool:
    lexical = _mask_tex_text_groups(value)
    # The canonical array column declaration is structural LaTeX, not prose.
    # Mask it as one token before the generic environment-name pass; otherwise
    # a declaration such as ``{cc}`` is misread as a two-letter word and the
    # serializer escapes the entire valid array into ``\text{...}``.
    lexical = re.sub(
        r"\\begin\{array\}\{[^{}\r\n]+\}", " ", lexical,
    )
    lexical = re.sub(
        r"\\(?:begin|end)\{[^}]*\}", " ", lexical,
    )
    lexical = re.sub(r"\\[A-Za-z]+", " ", lexical)
    lexical = re.sub(r"\\.", " ", lexical)
    return re.search(r"[A-Za-z]{2,}", lexical) is not None


def answer_cell_issues(answer_type: str, content: str) -> list[str]:
    """Validate a type-declared answer cell by syntax, never by meaning."""

    kind = str(answer_type or "").strip().lower()
    value = str(content or "")
    issues: list[str] = []
    if (
        _UNSUPPORTED_TABLE_BEGIN_RE.search(value)
        or _has_markdown_table(value)
        or _has_noncanonical_array(value)
    ):
        issues.append("unsupported_table")

    if kind == "equation":
        if _UNSUPPORTED_KATEX_COMMAND_RE.search(value):
            issues.append("equation_unsupported_command")
        if _RAW_ROW_SPACING_RE.search(value):
            issues.append("equation_row_spacing")
        if _KATEX_TOKEN_RE.search(value) or _KATEX_LIKE_TAG_RE.search(value):
            issues.append("equation_katex_wrapper")
        delimiter_value = _RAW_ROW_SPACING_RE.sub("", value)
        if (
            _has_raw_math(delimiter_value)
            or _has_unescaped_dollar(delimiter_value)
            or _has_raw_tex_math_delimiter(delimiter_value)
        ):
            issues.append("equation_math_delimiter")
        if (
            _IMAGE_TAG_RE.search(value)
            or _MARKDOWN_IMAGE_RE.search(value)
            or _MARKDOWN_LINK_RE.search(value)
            or _BARE_URL_RE.search(value)
        ):
            issues.append("equation_non_latex_markup")
        # Whole-cell Equation rendering means prose must be explicit TeX
        # text, not English left loose between math spans.  This lexical gate
        # masks balanced text/style atoms and command/environment names, then
        # rejects any remaining multi-letter ASCII run.  It does not decide
        # what the words or equation mean.
        if _equation_has_loose_prose(value):
            issues.append("equation_plain_text")
    elif kind == "phrases":
        if _KATEX_TOKEN_RE.search(value) or _KATEX_LIKE_TAG_RE.search(value):
            issues.append("phrases_katex")
        if (
            _RAW_LATEX_RE.search(value)
            or _RAW_SCRIPT_TAIL_RE.search(value)
            or re.search(r"\\[A-Za-z]+", value)
        ):
            issues.append("phrases_latex")
        if (
            _has_raw_math(value)
            or _has_unescaped_dollar(value)
            or _has_raw_tex_math_delimiter(value)
        ):
            issues.append("phrases_math_delimiter")
        if (
            _IMAGE_TAG_RE.search(value)
            or _MARKDOWN_IMAGE_RE.search(value)
            or _MARKDOWN_LINK_RE.search(value)
            or _BARE_URL_RE.search(value)
        ):
            issues.append("phrases_markup")
    elif kind == "image":
        # An Image cell's whole contract is carrying a retrievable source
        # visual onto the CMS wire (``raw_answer_cell`` reduces a lone
        # canonical tag to its src URL).  Both checks are syntax, not
        # meaning: a cell that declares the Image medium yet names no image
        # source at all cannot render anywhere downstream — the 2026-08-27
        # owner audit measured exactly this as source figures silently
        # absent from Assessments and Rubrics.  Which image belongs stays
        # the model's call; this only refuses a sourceless Image cell.
        if not (
            _CANONICAL_IMAGE_TAG_RE.search(value)
            or _MARKDOWN_IMAGE_RE.search(value)
            or _BARE_URL_RE.search(value)
        ):
            issues.append("image_missing_source")
        # Equation markup cannot ride in an Image cell.  Image sources are
        # masked first so URL paths with underscores or backslash-free tags
        # never read as TeX.
        caption = _CANONICAL_IMAGE_TAG_RE.sub(" ", value)
        caption = _MARKDOWN_IMAGE_RE.sub(" ", caption)
        caption = _BARE_URL_RE.sub(" ", caption)
        if (
            _KATEX_TOKEN_RE.search(caption)
            or _KATEX_LIKE_TAG_RE.search(caption)
            or re.search(r"\\[A-Za-z]+", caption)
        ):
            issues.append("image_katex_markup")
    return list(dict.fromkeys(issues))


_GROUP_MATH_CHARS_RE = re.compile(
    r"[A-Za-z0-9\\{}_^+\-*/=<>.,%()\[\]\s\u00d7\u00f7]+"
)


def _balanced_math_group_end(value: str, start: int) -> int | None:
    """Return a balanced, single-line group containing only math-like text."""
    if start >= len(value) or value[start] not in "([":
        return None
    opening = value[start]
    closing = ")" if opening == "(" else "]"
    group_end = _balanced_group_end(
        value, start, opening, closing)
    if group_end is None:
        return None
    body = value[start + 1:group_end - 1].strip()
    if (
        not body
        or "\n" in body
        or _GROUP_MATH_CHARS_RE.fullmatch(body) is None
        or (body.isalpha() and len(body) > 3)
        or (
            re.search(r"\s", body)
            and re.search(
                r"[_^+\-*/=<>\u00d7\u00f7\\]", body,
            ) is None
        )
    ):
        return None
    return group_end


def _consume_scripts(value: str, start: int) -> tuple[int, bool] | None:
    cursor = start
    scripted = False
    while cursor < len(value) and value[cursor] in "_^":
        scripted = True
        argument_start = cursor + 1
        if argument_start >= len(value):
            return None
        if value[argument_start] == "{":
            group_end = _balanced_group_end(
                value, argument_start, "{", "}")
            if group_end is None:
                return None
            cursor = group_end
        elif value[argument_start] in "([":
            group_end = _balanced_math_group_end(
                value, argument_start)
            if group_end is None:
                return None
            cursor = group_end
        elif value[argument_start] == "\\":
            argument = _math_atom(
                value, argument_start, consume_scripts=False)
            if argument is None:
                return None
            cursor = argument[0]
        elif value[argument_start].isalnum():
            cursor = argument_start + 1
        else:
            return None
    return cursor, scripted


def _math_atom(
    value: str, start: int, *, consume_scripts: bool = True,
) -> tuple[int, bool] | None:
    """Parse one conservative math atom as ``(exclusive_end, strong_seed)``."""
    def finish(end: int, strong: bool) -> tuple[int, bool] | None:
        if not consume_scripts:
            return end, strong
        scripted = _consume_scripts(value, end)
        if scripted is None:
            return None
        return scripted[0], strong or scripted[1]

    if start >= len(value):
        return None
    character = value[start]
    if character.isdigit():
        number = re.match(r"\d+(?:\.\d+)?", value[start:])
        end = start + len(number.group(0)) if number else start + 1
        return finish(end, False)
    if character.isalpha():
        end = start + 1
        while end < len(value) and value[end].isalpha():
            end += 1
        if end != start + 1:
            return None
        return finish(end, False)
    if character in "([":
        group_end = _balanced_math_group_end(value, start)
        if group_end is None:
            return None
        if not consume_scripts:
            return None
        scripted = _consume_scripts(value, group_end)
        if scripted is None or not scripted[1]:
            return None
        return scripted[0], True
    if character != "\\":
        return None

    command = re.match(r"\\(?:[A-Za-z]+|[%_#{}])", value[start:])
    if command is None:
        return None
    token = command.group(0)[1:]
    cursor = start + len(command.group(0))
    if token in {"%", "_", "#", "{", "}"}:
        return finish(cursor, True)
    name = token.lower()
    if name in _TEX_NOARG_COMMANDS:
        return finish(cursor, True)
    group_count = _TEX_COMMAND_GROUPS.get(name)
    if group_count is None:
        return None
    if name == "sqrt":
        optional_start = cursor
        while optional_start < len(value) and value[optional_start].isspace():
            optional_start += 1
        if optional_start < len(value) and value[optional_start] == "[":
            optional_end = _balanced_group_end(
                value, optional_start, "[", "]")
            if optional_end is None:
                return None
            cursor = optional_end
    for _ in range(group_count):
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        if cursor >= len(value) or value[cursor] != "{":
            return None
        group_end = _balanced_group_end(value, cursor, "{", "}")
        if group_end is None:
            return None
        cursor = group_end
    return finish(cursor, True)


def _is_tex_operator_atom(value: str, start: int) -> bool:
    command = re.match(r"\\([A-Za-z]+)", value[start:])
    return bool(
        command and command.group(1).lower() in _TEX_OPERATOR_COMMANDS)


def _raw_math_ranges(value: str) -> list[tuple[int, int]]:
    """Find complete, conservatively tokenized expressions with TeX seeds."""
    ranges: list[tuple[int, int]] = []
    tokens: list[tuple[str, int, int, bool]] = []

    def flush() -> None:
        nonlocal tokens
        if not tokens or not any(token[3] for token in tokens):
            tokens = []
            return
        atom_indexes = [
            index for index, token in enumerate(tokens)
            if token[0] == "atom"
        ]
        if not atom_indexes:
            tokens = []
            return
        first, last = atom_indexes[0], atom_indexes[-1]
        while (
            first > 0
            and tokens[first - 1][0]
            in {"open_paren", "open_bracket", "unary"}
        ):
            first -= 1
        stack: list[str] = []
        valid = True
        for kind, _, _, _ in tokens[first:last + 1]:
            if kind == "open_paren":
                stack.append("(")
            elif kind == "open_bracket":
                stack.append("[")
            elif kind == "close_paren":
                if not stack or stack.pop() != "(":
                    valid = False
                    break
            elif kind == "close_bracket":
                if not stack or stack.pop() != "[":
                    valid = False
                    break
        while valid and stack and last + 1 < len(tokens):
            kind = tokens[last + 1][0]
            expected = ")" if stack[-1] == "(" else "]"
            actual = ")" if kind == "close_paren" else (
                "]" if kind == "close_bracket" else "")
            if actual != expected:
                break
            stack.pop()
            last += 1
        if valid and not stack:
            ranges.append((tokens[first][1], tokens[last][2]))
        tokens = []

    cursor = 0
    separated_by_whitespace = False
    while cursor < len(value):
        if value[cursor].isspace():
            separated_by_whitespace = True
            cursor += 1
            continue
        atom = _math_atom(value, cursor)
        if atom is not None:
            end, strong = atom
            if separated_by_whitespace and tokens:
                previous = tokens[-1]
                previous_is_tex = (
                    previous[0] == "atom"
                    and value[previous[1]] == "\\"
                )
                current_is_tex = value[cursor] == "\\"
                weak_tex_boundary = (
                    previous[0] == "atom"
                    and (
                        (not previous[3] and current_is_tex)
                        or (previous_is_tex and not strong)
                    )
                )
                explicit_operator = (
                    (
                        previous_is_tex
                        and _is_tex_operator_atom(value, previous[1])
                    )
                    or (
                        current_is_tex
                        and _is_tex_operator_atom(value, cursor)
                    )
                )
                if weak_tex_boundary and not explicit_operator:
                    flush()
            tokens.append(("atom", cursor, end, strong))
            cursor = end
            separated_by_whitespace = False
            continue
        if value.startswith("...", cursor):
            tokens.append(("operator", cursor, cursor + 3, False))
            cursor += 3
            separated_by_whitespace = False
            continue
        character = value[cursor]
        if character in _MATH_OPERATOR_CHARS:
            kind = {
                "(": "open_paren",
                "[": "open_bracket",
                ")": "close_paren",
                "]": "close_bracket",
            }.get(character, "operator")
            if character in "+-" and not any(
                token[0] == "atom" for token in tokens
            ):
                kind = "unary"
            tokens.append((kind, cursor, cursor + 1, False))
            cursor += 1
            separated_by_whitespace = False
            continue
        flush()
        separated_by_whitespace = False
        if character.isalpha():
            cursor += 1
            while cursor < len(value) and value[cursor].isalpha():
                cursor += 1
        else:
            cursor += 1
    flush()

    for match in _RAW_EQUATION_RE.finditer(value):
        left = re.sub(r"\s+", "", match.group("left"))
        right = re.sub(r"\s+", "", match.group("right"))
        has_arithmetic = bool(re.search(
            r"[+\-*/\u00d7\u00f7]", left + right))
        if (
            re.fullmatch(r"[A-Za-z]|\d+(?:\.\d+)?", left)
            or re.search(r"[+\-*/\u00d7\u00f7]", left)
        ):
            ranges.append(match.span())
        elif has_arithmetic:
            ranges.append(match.span("right"))
    return ranges


def is_unambiguous_math_expression(text: str) -> bool:
    """Return whether all non-whitespace text is one conservative math range."""
    value = str(text or "").strip()
    if not value:
        return False
    operand = r"(?:[A-Za-z]|\d+(?:\.\d+)?)"
    if re.fullmatch(
        rf"{operand}(?:\s*[+\-*/\u00d7\u00f7]\s*{operand})+",
        value,
    ):
        return True
    merged: list[tuple[int, int]] = []
    for start, end in sorted(set(_raw_math_ranges(value))):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged == [(0, len(value))]


def repair_unwrapped_math(text: str) -> str:
    """Wrap unambiguous bare TeX/equations without changing their content.

    This is intentionally narrower than ``canonicalize_rich_text``. It is a
    deterministic late-repair helper for rows already diagnosed only with
    ``raw_latex`` and/or ``raw_math_expression``. Existing rich-text markup,
    links, images, and code remain byte-for-byte protected. Malformed TeX
    arguments are left untouched for the guarded model-repair fallback.
    """
    value = replace_unsupported_tables(str(text or ""))
    protected: list[str] = []

    def stash(match: re.Match | str) -> str:
        token = f"\ue000{len(protected)}\ue001"
        protected.append(
            match.group(0) if isinstance(match, re.Match) else str(match)
        )
        return token

    value = _replace_markdown_code(value, stash)
    for pattern in (
        _KATEX_TAG_RE,
        _MARKDOWN_IMAGE_RE,
        _IMAGE_TAG_RE,
        _MARKDOWN_LINK_RE,
        _BARE_URL_RE,
    ):
        value = pattern.sub(stash, value)

    ranges = _raw_math_ranges(value)
    if not ranges:
        repaired = value
    else:
        merged: list[tuple[int, int]] = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        repaired = value
        for start, end in reversed(merged):
            repaired = (
                repaired[:start]
                + katex(repaired[start:end])
                + repaired[end:]
            )

    for index, original in enumerate(protected):
        repaired = repaired.replace(f"\ue000{index}\ue001", original)
    return repaired


def canonicalize_rich_text(text: str) -> str:
    """Normalize generated rich text to the exact Bulk Import wire format.

    Existing lower-case tags remain accepted as input, but output always uses
    ``[Katex]``. Common MMD math delimiters and Markdown/LaTeX image syntax are
    converted before concept rows are persisted. The helper deliberately does
    not touch typed keyword columns, whose Equation contract is raw LaTeX.
    """
    value = replace_unsupported_tables(str(text or ""))
    protected: list[str] = []

    def stash(rendered: str) -> str:
        token = f"@@AEGIS_RICH_TEXT_{len(protected):04d}@@"
        protected.append(rendered)
        return token

    def existing_katex(match: re.Match) -> str:
        body = (match.group("body") or "").strip()
        return stash(katex(body)) if body else ""

    value = _KATEX_TAG_RE.sub(existing_katex, value)

    def markdown_image(match: re.Match) -> str:
        alt = (match.group("alt") or "").strip() or "Source visual"
        return stash(image(match.group("src"), alt))

    value = _MARKDOWN_IMAGE_RE.sub(markdown_image, value)
    value = _INCLUDEGRAPHICS_RE.sub(
        lambda match: stash(image(match.group("src"), "Source visual")),
        value,
    )

    value = _FOOTNOTE_RE.sub(lambda match: match.group("body").strip(), value)
    # Models occasionally emit a literal trailing ``\n`` escape in prose.
    # Convert only delimiter-shaped escapes; a TeX command such as ``\nu``
    # remains untouched and is rejected outside a canonical KaTeX span.
    value = re.sub(r"\\n\s*$", "\n", value)

    for pattern in _RAW_BLOCK_MATH_PATTERNS:
        value = pattern.sub(
            lambda match: stash(katex(match.group("body"))),
            value,
        )
    value = _SINGLE_DOLLAR_MATH_RE.sub(
        lambda match: (
            match.group(0)
            if _looks_like_currency_pair(match)
            else stash(katex(match.group("body")))
        ),
        value,
    )

    for index, rendered in enumerate(protected):
        value = value.replace(
            f"@@AEGIS_RICH_TEXT_{index:04d}@@", rendered)
    return value


def rich_text_issues(
    text: str, *, require_canonical_case: bool = True,
) -> list[str]:
    """Return deterministic rich-text contract violations.

    The reader may set ``require_canonical_case=False`` while importing legacy
    lower-case tags. Newly generated concept/workbook content uses the stricter
    default so malformed free-form TeX cannot silently ship.
    """
    value = str(text or "")
    issues: list[str] = []
    if (
        _UNSUPPORTED_TABLE_BEGIN_RE.search(value)
        or _has_markdown_table(value)
        or _has_noncanonical_array(value)
    ):
        issues.append("unsupported_table")
    if _UNSUPPORTED_KATEX_COMMAND_RE.search(value):
        issues.append("unsupported_katex_command")
    if _RAW_ROW_SPACING_RE.search(value):
        issues.append("katex_row_spacing")
    tokens = list(_KATEX_TOKEN_RE.finditer(value))
    depth = 0
    malformed_order = False
    nested = False
    for token in tokens:
        if token.group("close"):
            if depth == 0:
                malformed_order = True
            else:
                depth -= 1
        else:
            if depth:
                nested = True
            depth += 1
    if depth or malformed_order:
        issues.append("unbalanced_katex")
    if nested:
        issues.append("nested_katex")
    if re.search(r"\[katex\]\s*\[/katex\]", value, re.IGNORECASE):
        issues.append("empty_katex")
    malformed_katex = [
        match.group(0)
        for match in _KATEX_LIKE_TAG_RE.finditer(value)
        if not _KATEX_TOKEN_RE.fullmatch(match.group(0))
    ]
    if (
        malformed_katex
        or len(re.findall(r"\[/?katex\b", value, re.IGNORECASE))
        != len(list(_KATEX_LIKE_TAG_RE.finditer(value)))
    ):
        issues.append("malformed_katex")
    if require_canonical_case and any(
        token.group(0)
        != ("[/Katex]" if token.group("close") else "[Katex]")
        for token in tokens
    ):
        issues.append("noncanonical_katex_case")

    # Wrappers establish the rendering medium; they do not make a second set
    # of raw delimiters valid.  Inspect their bodies before masking the spans
    # from the rich-text checks below.
    for match in _KATEX_TAG_RE.finditer(value):
        body = str(match.group("body") or "")
        delimiter_body = _RAW_ROW_SPACING_RE.sub("", body)
        if (
            _has_raw_math(delimiter_body)
            or _has_unescaped_dollar(delimiter_body)
            or _has_raw_tex_math_delimiter(delimiter_body)
        ):
            issues.append("raw_math_delimiter")

    masked = _KATEX_TAG_RE.sub("", value)
    if re.search(r"!\[", masked):
        issues.append("markdown_image")
    math_masked = _replace_markdown_code(
        _IMAGE_TAG_RE.sub(
            "", _MARKDOWN_LINK_RE.sub("", masked)),
        lambda _value: "",
    )
    if _LITERAL_NEWLINE_BEFORE_LIST_ITEM_RE.search(math_masked):
        issues.append("literal_newline_escape")
    delimiter_masked = _RAW_ROW_SPACING_RE.sub(
        "", _CURRENCY_TOKEN_RE.sub("", math_masked),
    )
    if (
        _has_raw_math(math_masked)
        or _has_unescaped_dollar(delimiter_masked)
        or _has_raw_tex_math_delimiter(delimiter_masked)
    ):
        issues.append("raw_math_delimiter")
    if (
        _RAW_LATEX_RE.search(math_masked)
        or _RAW_SCRIPT_TAIL_RE.search(math_masked)
    ):
        issues.append("raw_latex")
    if _has_raw_equation(delimiter_masked):
        issues.append("raw_math_expression")
    image_tags = list(_IMAGE_TAG_RE.finditer(value))
    if len(re.findall(r"\[img\b", value, re.IGNORECASE)) != len(image_tags):
        issues.append("unbalanced_image")
    for match in image_tags:
        tag = match.group(0)
        canonical = _CANONICAL_IMAGE_TAG_RE.fullmatch(tag)
        attrs_match = re.match(r"\[img(?P<attrs>.*)\]", tag, re.IGNORECASE)
        attrs = attrs_match.group("attrs") if attrs_match else ""
        src_match = re.search(r'\bsrc="([^"]+)"', attrs, re.IGNORECASE)
        if src_match is None:
            issues.append("invalid_image_src")
        else:
            try:
                _public_http_url(
                    src_match.group(1),
                    label="image src",
                    require_https=True,
                )
            except ValueError:
                issues.append("invalid_image_src")
        alt = re.search(r'\balt="([^"]*)"', attrs, re.IGNORECASE)
        if alt is None or not alt.group(1).strip():
            issues.append("missing_image_alt")
        if canonical is None:
            issues.append("noncanonical_image")
    return list(dict.fromkeys(issues))


# Preamble injected as a system / instruction prefix in the live OpenAI
# generation path so the LLM emits rich-text content in the same bracket
# format the importer expects. Registered so it is editable from the Admin tab.
from . import prompts as _prompts  # noqa: E402

_PROMPT_PREAMBLE_DEFAULT = """\
Rich-text rules for the question, display_answer, and answer_explanation
columns:
  - Plain text is typed directly.
  - Equations MUST be wrapped: [Katex] LaTeX [/Katex]. Never use raw $, $$,
    \\(...\\), or \\[...\\] delimiters.
    Inline vs. block mode is auto-detected from the content (presence of
    \\begin, \\array, \\frac, \\sum, \\int, \\prod, or \\oint triggers block).
  - A textual table may use the canonical form
    [Katex] \\begin{array}{...}...\\end{array} [/Katex]. Keep the entire array
    inside one wrapper and use ordinary \\\\ row breaks with no spacing option.
  - Images: [img src="https://..." alt="..."]. Use double quotes only;
    src must be a full public HTTPS URL and must come before alt. No other
    attributes are allowed.
  - Links: [Display Text](https://full-url). Wrap raw URLs; never emit a
    bare URL on its own.

Type-declared answer_content and keyword cells use exactly ONE medium:
  - Equation: the WHOLE cell is raw LaTeX. Never include [Katex] wrappers,
    math delimiters, an image/link, or plain prose outside a TeX text atom.
    A textual table is raw \\begin{array}{...}...\\end{array} in this cell.
  - Phrases: the WHOLE cell is plain text. Never include LaTeX, [Katex], math
    delimiters, or image/link markup.
  - Never mix prose and wrapped/raw maths in one answer/rubric block. If an
    Equation cell needs words, encode them inside \\text{...} so the complete
    cell remains valid raw LaTeX.

Canonical KaTeX arrays are supported as described above. LaTeX tabular and
Markdown pipe tables remain unsupported. When the source already supplies a
table containing an image, preserve the complete source-table image rather
than fragment screenshots. Otherwise retain every cell as mechanically
labelled plain text (for example, "Table row 1, column 2: 8611"); never emit
tabular markup or reconstruct missing meaning.

Forbidden: raw math delimiters, nested [Katex], single-quoted img attrs,
empty [Katex] tags, raw LaTeX outside a [Katex] tag, Markdown images,
raw tabular/footnote commands, noncanonical or unterminated arrays, the
unsupported commands \\mathrm, \\hspace, \\phantom, and \\boxed, and optional
row spacing such as \\\\[0.4cm]. Use \\text{...} for words in mathematics.
"""

_prompts.register(
    "content.katex_rules",
    label="Rich-text / KaTeX formatting rules",
    category="Shared formatting",
    description="Injected into every assessment-generation prompt so questions "
                "and answers use the canonical [Katex]/[img]/[link] format.",
    default=_PROMPT_PREAMBLE_DEFAULT,
)


def __getattr__(name: str) -> str:
    # Resolve PROMPT_PREAMBLE lazily so Admin edits apply on the next run.
    if name == "PROMPT_PREAMBLE":
        return _prompts.get_text("content.katex_rules")
    raise AttributeError(name)
