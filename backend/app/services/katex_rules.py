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

Keyword columns are NOT rich text; they hold direct KaTeX (no wrappers).
"""
from __future__ import annotations

import html
import re
from collections.abc import Callable
from urllib.parse import urlsplit

# Canonical Bulk Import field names that accept rich text.
RICH_TEXT_FIELDS = frozenset({
    "question", "answer_content", "answer", "display_answer", "answer_explanation",
    "concept_details",
})
# Sub-question keyword field; raw KaTeX, no [Katex] wrapper.
RAW_KATEX_FIELDS = frozenset({"keyword"})

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
    r"\\begin\{tabular\}\{(?P<columns>[^}]*)\}"
    r"(?P<body>.*?)\\end\{tabular\}",
    re.IGNORECASE | re.DOTALL,
)
_FOOTNOTE_RE = re.compile(
    r"\\footnotetext\{(?P<body>[^{}]*(?:\{[^{}]*\}[^{}]*)*)\}",
    re.IGNORECASE | re.DOTALL,
)
_RAW_BLOCK_MATH_PATTERNS = (
    re.compile(r"\$\$(?P<body>.+?)\$\$", re.DOTALL),
    re.compile(r"\\\[(?P<body>.+?)\\\]", re.DOTALL),
    re.compile(r"\\\((?P<body>.+?)\\\)", re.DOTALL),
)
_SINGLE_DOLLAR_MATH_RE = re.compile(
    r"(?<!\\)\$(?!\$)(?P<body>[^$\n]+?)(?<!\\)\$(?!\$)"
)
_RAW_MATH_PATTERNS = (*_RAW_BLOCK_MATH_PATTERNS, _SINGLE_DOLLAR_MATH_RE)
_KATEX_TOKEN_RE = re.compile(r"\[(?P<close>/)?katex\]", re.IGNORECASE)
_KATEX_LIKE_TAG_RE = re.compile(r"\[/?katex\b[^\]]*\]", re.IGNORECASE)
_IMAGE_TAG_RE = re.compile(r"\[img\b[^\]]*\]", re.IGNORECASE)
_CANONICAL_IMAGE_TAG_RE = re.compile(
    r'\[img src="(?P<src>https://[^"]+)" alt="(?P<alt>[^"]+)"\]'
)
_MARKDOWN_LINK_RE = re.compile(
    r"\[[^\]]+\]\(https?://(?:[^()\s]|(?:\([^()]*\)))+\)",
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
    value = str(text or "")
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
    not touch raw keyword columns, whose workbook contract is direct KaTeX.
    """
    value = str(text or "")
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

    # KaTeX supports array rather than LaTeX's text-mode tabular environment.
    def tabular(match: re.Match) -> str:
        columns = re.sub(r"[^clr|]", "", match.group("columns") or "") or "c"
        body = re.sub(r"\\hline\b", "", match.group("body") or "").strip()
        return stash(katex(
            rf"\begin{{array}}{{{columns}}}{body}\end{{array}}"
        ))

    value = _TABULAR_RE.sub(tabular, value)
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

    masked = _KATEX_TAG_RE.sub("", value)
    if re.search(r"!\[", masked):
        issues.append("markdown_image")
    math_masked = _replace_markdown_code(
        _IMAGE_TAG_RE.sub(
            "", _MARKDOWN_LINK_RE.sub("", masked)),
        lambda _value: "",
    )
    delimiter_masked = _CURRENCY_TOKEN_RE.sub("", math_masked)
    if (
        _has_raw_math(math_masked)
        or re.search(r"(?<!\\)\$", delimiter_masked)
        or re.search(r"\\[\[\]()]|(?<!\\)\$\$", delimiter_masked)
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
Rich-text rules for the question, answer_content, display_answer, and
answer_explanation columns:
  - Plain text is typed directly.
  - Equations MUST be wrapped: [Katex] LaTeX [/Katex]. Never use raw $, $$,
    \\(...\\), or \\[...\\] delimiters.
    Inline vs. block mode is auto-detected from the content (presence of
    \\begin, \\array, \\frac, \\sum, \\int, \\prod, or \\oint triggers block).
  - Images: [img src="https://..." alt="..."]. Use double quotes only;
    src must be a full public HTTPS URL and must come before alt. No other
    attributes are allowed.
  - Links: [Display Text](https://full-url). Wrap raw URLs; never emit a
    bare URL on its own.

Keyword columns hold direct KaTeX (no [Katex] wrappers, no markdown).

Forbidden: raw math delimiters, nested [Katex], single-quoted img attrs,
empty [Katex] tags, raw LaTeX outside a [Katex] tag, Markdown images,
raw tabular/footnote commands, and [0.4cm]-style spacing.
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
