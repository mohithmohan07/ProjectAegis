"""KaTeX / rich-text content rules for assessment generation.

The Bulk Import workbook's rich-text columns accept a strict subset of
markup. These rules are the single source of truth shared by:
  - the synthetic dry-mode generator (emits sample content in this format)
  - the live (OpenAI) generation prompt (injected as a preamble)

Allowed in rich-text columns
  Plain text   - typed directly
  Equation     - [katex] LaTeX [/katex]   (inline/block auto-detected)
  Image        - [img src="https://..." alt="..."]
  Link         - [Display Text](https://...)

Keyword columns are NOT rich text; they hold direct KaTeX (no wrappers).
"""
from __future__ import annotations

# Canonical Bulk Import field names that accept rich text.
RICH_TEXT_FIELDS = frozenset({
    "question", "answer_content", "answer", "answer_display", "answer_explanation",
})
# Sub-question keyword field; raw KaTeX, no [katex] wrapper.
RAW_KATEX_FIELDS = frozenset({"keyword"})

# Tokens whose presence switches KaTeX to block (display) mode.
_BLOCK_TRIGGERS = (r"\begin", r"\array", r"\frac", r"\sum", r"\int", r"\prod", r"\oint")


def katex(latex: str) -> str:
    return f"[katex] {latex.strip()} [/katex]"


def is_block(latex: str) -> bool:
    return any(tok in latex for tok in _BLOCK_TRIGGERS)


def image(src: str, alt: str, *, width: str | None = None, height: str | None = None) -> str:
    if not (src.startswith("https://") or src.startswith("http://")):
        raise ValueError("image src must be a full public http(s):// URL")
    tag = f'[img src="{src}" alt="{alt}"'
    if width:
        tag += f' width="{width}"'
    if height:
        tag += f' height="{height}"'
    return tag + "]"


def link(text: str, url: str) -> str:
    if not (url.startswith("https://") or url.startswith("http://")):
        raise ValueError("link url must be absolute http(s)://")
    return f"[{text}]({url})"


# Preamble injected as a system / instruction prefix in the live OpenAI
# generation path so the LLM emits rich-text content in the same bracket
# format the importer expects. Registered so it is editable from the Admin tab.
from . import prompts as _prompts  # noqa: E402

_PROMPT_PREAMBLE_DEFAULT = """\
Rich-text rules for the question, answer_content, answer_display, and
answer_explanation columns:
  - Plain text is typed directly.
  - Equations MUST be wrapped: [katex] LaTeX [/katex]. Never use $$...$$.
    Inline vs. block mode is auto-detected from the content (presence of
    \\begin, \\array, \\frac, \\sum, \\int, \\prod, or \\oint triggers block).
  - Images: [img src="https://..." alt="..."]. Use double quotes only;
    src must be a full public http(s) URL and must come before alt.
    Optional: width="N" / width="N%" / height="N".
  - Links: [Display Text](https://full-url). Wrap raw URLs; never emit a
    bare URL on its own.

Keyword columns hold direct KaTeX (no [katex] wrappers, no markdown).

Forbidden: raw $$...$$ delimiters, nested [katex], single-quoted img attrs,
empty [katex] tags, raw LaTeX outside a [katex] tag, [0.4cm]-style spacing.
"""

_prompts.register(
    "content.katex_rules",
    label="Rich-text / KaTeX formatting rules",
    category="Shared formatting",
    description="Injected into every assessment-generation prompt so questions "
                "and answers use the canonical [katex]/[img]/[link] format.",
    default=_PROMPT_PREAMBLE_DEFAULT,
)


def __getattr__(name: str) -> str:
    # Resolve PROMPT_PREAMBLE lazily so Admin edits apply on the next run.
    if name == "PROMPT_PREAMBLE":
        return _prompts.get_text("content.katex_rules")
    raise AttributeError(name)
