"""Bind already-owned assessment figures to verified pixels and replay identity.

Only explicit image wires/references are collected. No semantic image selection,
network fetch, neighbouring-page inference, or model-selected URL dereferencing
occurs here. Unavailable evidence remains visible and produces review flags.
"""
from __future__ import annotations

import base64
import hashlib
import io
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

from . import source_asset_publication as publication
from . import source_asset_store

VERSION = "assessment-visual-evidence-1"
INSTRUCTION = (
    "visual_evidence binds each supplied figure to its actual image input by "
    "source URL and content hash. Inspect the attached pixels, including labels, "
    "units and legends, together with the text. A URL or caption alone is not "
    "visual evidence. For any unavailable figure, explicitly name the missing "
    "evidence; never claim to have inspected it or invent its contents."
)


def _references(value: Any) -> list[str]:
    found = set(publication.image_urls(value))

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key).startswith("_") or key == "visual_evidence":
                    continue
                if key == "image_urls" and isinstance(child, (list, tuple)):
                    found.update(str(url).strip() for url in child if isinstance(url, str) and url.strip())
                walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child)

    walk(value)
    return sorted(found)


def _image(url: str) -> tuple[dict[str, Any], bytes | None]:
    evidence: dict[str, Any] = {"source_url": url, "state": "unavailable"}
    try:
        parsed = urlsplit(url)
        origin = publication.configured_origin()
        route = publication._ROUTE.fullmatch(parsed.path)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        # The producer emits one advisory 40-hex sig. As in source_assets.py,
        # delivery is bound to verified content bytes rather than a rotatable
        # signing secret. Accept that exact query grammar, retaining the URL;
        # never forward/fetch a supplied query or accept other parameters.
        producer_query = not query or (
            len(query) == 1 and query[0][0] == "sig"
            and len(query[0][1]) == 40
            and all(character in "0123456789abcdef" for character in query[0][1])
        )
        if not (
            publication.valid_public_origin(origin)
            and publication._origin(url) == publication._origin(origin)
            and route and producer_query and not parsed.fragment
            and not parsed.username and not parsed.password
        ):
            evidence["reason"] = "not_an_authorized_pinned_source_asset"
            return evidence, None
        filename = route[2]
        evidence["sha256"] = filename.removesuffix(".jpg")
        path = source_asset_store.stored_asset_path(filename)
        if not path.is_file():
            evidence["reason"] = "pinned_pixels_unavailable"
            return evidence, None
        if path.stat().st_size > publication.MAX_ASSET_BYTES:
            evidence["reason"] = "image_byte_limit_exceeded"
            return evidence, None
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != evidence["sha256"]:
            evidence["reason"] = "content_hash_mismatch"
            return evidence, None
        from PIL import Image

        with Image.open(io.BytesIO(data)) as decoded:
            if decoded.format != "JPEG" or decoded.width * decoded.height > publication.MAX_IMAGE_PIXELS:
                evidence["reason"] = "invalid_or_oversized_image"
                return evidence, None
            decoded.load()
            evidence.update(width=decoded.width, height=decoded.height)
        evidence.update(state="attached", media_type="image/jpeg")
        return evidence, data
    except Exception as exc:  # malformed/oversized decoder inputs remain evidence flags
        evidence["reason"] = "unreadable_image_evidence"
        evidence["error_type"] = type(exc).__name__
        return evidence, None


def bind(payload: dict[str, Any], *evidence: Any) -> dict[str, Any]:
    """Bind identities before decide-once; only named supplied evidence is read."""
    entries = [_image(url)[0] for url in _references(evidence or (payload,))]
    payload["visual_evidence"] = {
        "version": VERSION, "instruction": INSTRUCTION, "images": entries,
    }
    return payload


def image_inputs(payload: Mapping[str, Any]) -> list[str]:
    """Resolve the sealed evidence into image inputs without persisting base64."""
    bound = payload.get("visual_evidence")
    if not isinstance(bound, Mapping):
        return []
    images: list[str] = []
    seen: set[str] = set()
    for entry in bound.get("images") or []:
        if not isinstance(entry, Mapping) or entry.get("state") != "attached":
            continue
        actual, data = _image(str(entry.get("source_url") or ""))
        if actual != dict(entry) or data is None:
            # A stale decision request cannot silently claim pixels it lost.
            raise ValueError("assessment visual evidence changed after binding")
        digest = str(actual["sha256"])
        if digest not in seen:
            seen.add(digest)
            images.append("data:image/jpeg;base64," + base64.b64encode(data).decode("ascii"))
    return images


def review_flags(payload: Mapping[str, Any]) -> list[str]:
    bound = payload.get("visual_evidence") or {}
    return [
        "assessment_visual_evidence_unavailable: "
        + str(entry.get("source_url") or "") + " (" + str(entry.get("reason") or "") + ")"
        for entry in bound.get("images") or [] if entry.get("state") != "attached"
    ]
