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


def _public_http_url(value: str, *, label: str) -> str:
    value = str(value or "").strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or re.search(r"""[\s"'<>[\]]""", value)
    ):
        raise ValueError(f"{label} must be a full public http(s):// URL")
    return value


def _image_dimension(value: str, *, label: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"\d{1,4}%?", value):
        raise ValueError(f"image {label} must be a number or percentage")
    return value


def image(src: str, alt: str, *, width: str | None = None, height: str | None = None) -> str:
    src = _public_http_url(src, label="image src")
    safe_alt = html.escape(
        re.sub(r"\s+", " ", (alt or "").strip()), quote=True
    ).replace("[", "&#91;").replace("]", "&#93;")
    if not safe_alt:
        raise ValueError("image alt text is required")
    tag = f'[img src="{src}" alt="{safe_alt}"'
    if width:
        tag += f' width="{_image_dimension(width, label="width")}"'
    if height:
        tag += f' height="{_image_dimension(height, label="height")}"'
    return tag + "]"


def link(text: str, url: str) -> str:
    url = _public_http_url(url, label="link url")
    return f"[{text}]({url})"


_KATEX_TAG_RE = re.compile(
    r"\[katex\]\s*(?P<body>.*?)\s*\[/katex\]",
    re.IGNORECASE | re.DOTALL,
)
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<src>https?://[^)\s]+)\)",
    re.IGNORECASE,
)
_INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics(?:\[[^\]]*\])?\{(?P<src>https?://[^}\s]+)\}",
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
    r'\[img src="(?P<src>https?://[^"]+)" alt="(?P<alt>[^"]+)"'
    r'(?: width="(?P<width>\d{1,4}%?)")?'
    r'(?: height="(?P<height>\d{1,4}%?)")?\]'
)


def _looks_like_currency_pair(match: re.Match) -> bool:
    """Avoid interpreting ``$5 to $10`` as one inline equation."""
    body = (match.group("body") or "").strip()
    if not body or not body[0].isdigit():
        return False
    after = match.string[match.end():]
    if after[:1].isdigit():
        return True
    return bool(
        re.search(r"\s", body)
        and not re.search(r"""[\\_^{}=+\-*/<>]""", body)
    )


def _has_raw_math(value: str) -> bool:
    if any(pattern.search(value) for pattern in _RAW_BLOCK_MATH_PATTERNS):
        return True
    return any(
        not _looks_like_currency_pair(match)
        for match in _SINGLE_DOLLAR_MATH_RE.finditer(value)
    )


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
    if malformed_katex:
        issues.append("malformed_katex")
    if require_canonical_case and any(
        token.group(0)
        != ("[/Katex]" if token.group("close") else "[Katex]")
        for token in tokens
    ):
        issues.append("noncanonical_katex_case")

    masked = _KATEX_TAG_RE.sub("", value)
    if _MARKDOWN_IMAGE_RE.search(masked):
        issues.append("markdown_image")
    if _has_raw_math(masked):
        issues.append("raw_math_delimiter")
    if re.search(
        r"\\(?:begin|end|footnotetext|frac|dfrac|tfrac|sum|sqrt|"
        r"includegraphics)\b",
        masked,
        re.IGNORECASE,
    ):
        issues.append("raw_latex")
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
                _public_http_url(src_match.group(1), label="image src")
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
    src must be a full public http(s) URL and must come before alt.
    Optional: width="N" / width="N%" / height="N".
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
