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

import re

# Canonical Bulk Import field names that accept rich text.
RICH_TEXT_FIELDS = frozenset({
    "question", "answer_content", "answer", "answer_display", "answer_explanation",
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


def image(src: str, alt: str, *, width: str | None = None, height: str | None = None) -> str:
    if not (src.startswith("https://") or src.startswith("http://")):
        raise ValueError("image src must be a full public http(s):// URL")
    safe_alt = re.sub(r"\s+", " ", (alt or "").strip()).replace('"', "&quot;")
    if not safe_alt:
        raise ValueError("image alt text is required")
    tag = f'[img src="{src}" alt="{safe_alt}"'
    if width:
        tag += f' width="{width}"'
    if height:
        tag += f' height="{height}"'
    return tag + "]"


def link(text: str, url: str) -> str:
    if not (url.startswith("https://") or url.startswith("http://")):
        raise ValueError("link url must be absolute http(s)://")
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
_RAW_MATH_PATTERNS = (
    re.compile(r"\$\$(?P<body>.+?)\$\$", re.DOTALL),
    re.compile(r"\\\[(?P<body>.+?)\\\]", re.DOTALL),
    re.compile(r"\\\((?P<body>.+?)\\\)", re.DOTALL),
    # A paired single-dollar expression. Currency using ₹ is unaffected.
    re.compile(r"(?<!\\)\$(?!\$)(?P<body>[^$\n]+?)(?<!\\)\$(?!\$)"),
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

    for pattern in _RAW_MATH_PATTERNS:
        value = pattern.sub(
            lambda match: stash(katex(match.group("body"))),
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
    opens = re.findall(r"\[katex\]", value, re.IGNORECASE)
    closes = re.findall(r"\[/katex\]", value, re.IGNORECASE)
    if len(opens) != len(closes):
        issues.append("unbalanced_katex")
    if re.search(r"\[katex\]\s*\[/katex\]", value, re.IGNORECASE):
        issues.append("empty_katex")
    if require_canonical_case and (
        re.search(r"\[katex\]", value) or re.search(r"\[/katex\]", value)
    ):
        issues.append("noncanonical_katex_case")

    masked = _KATEX_TAG_RE.sub("", value)
    if _MARKDOWN_IMAGE_RE.search(masked):
        issues.append("markdown_image")
    if any(pattern.search(masked) for pattern in _RAW_MATH_PATTERNS):
        issues.append("raw_math_delimiter")
    if re.search(
        r"\\(?:begin|end|footnotetext|frac|dfrac|tfrac|sum|sqrt|"
        r"includegraphics)\b",
        masked,
        re.IGNORECASE,
    ):
        issues.append("raw_latex")
    for match in re.finditer(r"\[img(?P<attrs>[^\]]*)\]", value, re.IGNORECASE):
        attrs = match.group("attrs")
        if not re.search(r'\bsrc="https?://[^"]+"', attrs, re.IGNORECASE):
            issues.append("invalid_image_src")
        alt = re.search(r'\balt="([^"]*)"', attrs, re.IGNORECASE)
        if alt is None or not alt.group(1).strip():
            issues.append("missing_image_alt")
    return list(dict.fromkeys(issues))


# Preamble injected as a system / instruction prefix in the live OpenAI
# generation path so the LLM emits rich-text content in the same bracket
# format the importer expects. Registered so it is editable from the Admin tab.
from . import prompts as _prompts  # noqa: E402

_PROMPT_PREAMBLE_DEFAULT = """\
Rich-text rules for the question, answer_content, answer_display, and
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
