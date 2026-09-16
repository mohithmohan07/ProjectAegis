"""Protect inventory-owned Example text while formatting its outer grammar.

The flat Types format permits source questions containing literal Type/Case/
Example markers. Only an exact source comparison can distinguish those words
from container markers. Masking is temporary; the original raw slice is always
restored, including whitespace, math and image tags.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable

_EXAMPLE = re.compile(r"\bExamples?(?:\s+0*\d+)?\s*:\s*", re.IGNORECASE)
_BOUNDARY = re.compile(
    r"\b(?:(?:Miscellaneous\s+)?Type\s+\d+|Case\s+\d+|Examples?(?:\s+0*\d+)?)\s*:| // ",
    re.IGNORECASE,
)


def mask_examples(text: str, examples: Iterable[str], *,
                  comparison_key: Callable[[str], str] | None = None) -> tuple[str, Callable[[str], str]]:
    key = comparison_key or (lambda value: " ".join(value.split()))
    expected = {key(str(example)) for example in examples if str(example).strip()}
    expected.discard("")
    if not expected:
        return text, lambda value: value
    ends = sorted({match.start() for match in _BOUNDARY.finditer(text)} | {len(text)})
    spans: list[tuple[int, int]] = []
    for marker in _EXAMPLE.finditer(text):
        if spans and marker.start() < spans[-1][1]:
            continue
        start = marker.end()
        matches = [end for end in ends if end > start and key(text[start:end].rstrip()) in expected]
        if matches:
            end = max(matches)
            spans.append((start, len(text[:end].rstrip())))
    replacements: list[tuple[str, str]] = []
    masked = text
    for index, (start, end) in reversed(list(enumerate(spans))):
        token = "AEGISSOURCEQUOTE" + hashlib.sha256(f"{index}:{text}".encode()).hexdigest()
        while token in text:
            token += "X"
        replacements.append((token, text[start:end]))
        masked = masked[:start] + token + masked[end:]

    def restore(value: str) -> str:
        for token, raw in replacements:
            value = value.replace(token, raw)
        return value

    return masked, restore
