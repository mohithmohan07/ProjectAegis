from pathlib import Path

target = Path('backend/app/services/canonical_source_phase221_fallback.py')
text = target.read_text(encoding='utf-8')

def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    text = text.replace(old, new, 1)

replace_once(
    'FALLBACK_VERSION = "2.2.1"\nFALLBACK_COMPILER = "gpt-pdf-to-acsd-1"',
    'FALLBACK_VERSION = "2.2.2"\nFALLBACK_COMPILER = "gpt-pdf-to-acsd-2"',
    'fallback version',
)
replace_once(
    '    "heading",\n    "paragraph",\n    "task",',
    '    "heading",\n    "paragraph",\n    "source",\n    "task",',
    'source block kind',
)
replace_once(
    '_CACHE_DIR = config.DATA_DIR / "pdf-acsd-cache"\n',
    '_CACHE_DIR = config.DATA_DIR / "pdf-acsd-cache"\n'
    '_SOURCE_COMPATIBLE_KINDS = frozenset({"heading", "paragraph", "list", "other"})\n',
    'source-compatible kinds',
)
replace_once(
    '''def _max_output_tokens() -> int:\n    return max(4000, int(os.environ.get(\n        "AEGIS_GPT_PDF_ACSD_MAX_OUTPUT_TOKENS", "32000"\n    )))\n''',
    '''def _max_output_tokens() -> int:\n    return max(4000, int(os.environ.get(\n        "AEGIS_GPT_PDF_ACSD_MAX_OUTPUT_TOKENS", "32000"\n    )))\n\n\ndef _max_correction_attempts() -> int:\n    return max(0, min(3, int(os.environ.get(\n        "AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", "2"\n    ))))\n''',
    'correction attempt configuration',
)
replace_once(
    '''- paragraph/list/task: preserve exact wording, punctuation, numbering, and order.\n- task: separate learner instructions/questions from surrounding narrative and\n  preserve the visible source cue (Activity, Discuss, Project, etc.).\n- table: return every visible cell in table_rows.\n''',
    '''- paragraph/list: preserve exact wording, punctuation, numbering, and order.\n- source: use only for a visibly labelled historical source, excerpt, passage,\n  case study, or source box that is not itself a learner task. Preserve the\n  exact visible cue in source_label and the body in text.\n- task: separate learner instructions/questions from surrounding narrative and\n  preserve the visible task cue (Activity, Discuss, Project, etc.) in source_label.\n- source_label must be empty on heading, paragraph, list, table, figure, math,\n  and other blocks. Never attach a source-box cue to an ordinary paragraph.\n- table: return every visible cell in table_rows.\n''',
    'extraction block rules',
)

replace_once(
    '''and no content was invented. Do not rewrite or repair the candidate. Return
needs_correction or ambiguous when any material defect remains. Output strict
JSON only.
''',
    '''and no content was invented. A visibly labelled historical source, excerpt,
passage, case study, or source box that is not a learner task must use
kind=source and retain its exact cue in source_label. Do not rewrite or repair
the candidate. Return needs_correction or ambiguous when any material defect
remains. Output strict JSON only.
''',
    'verification source-block rule',
)

verification_marker = '''def _page_prompt(pages: list[PdfPage], *, candidate: dict[str, Any] | None = None) -> str:\n'''
correction_helpers = '''def _correction_system_prompt() -> str:\n    return """\nYou are the bounded Aegis PDF-to-ACSD correction reviewer. Compare the supplied\ncandidate and deterministic validation failure against the original PDF page\nimages. Return the complete corrected page batch, changing only fields or block\nroles required to resolve the stated defect. Preserve every visible word,\npunctuation mark, reading-order position, bounding box, table cell, formula, and\nfigure link unless the original page proves the candidate wrong.\n\nUse kind=source for a visibly labelled historical source, excerpt, passage, case\nstudy, or source box that is not a learner task. Put its exact visible cue in\nsource_label and its body in text. Use kind=task only for learner instructions or\nquestions, with the exact task cue in source_label. source_label must be empty on\nall other block kinds. Never discard a visible cue merely to satisfy the schema.\nOutput the complete strict JSON pages array only.\n""".strip()\n\n\ndef _correction_prompt(\n    pages: list[PdfPage],\n    *,\n    candidate: dict[str, Any],\n    reason: str,\n) -> str:\n    ledger = [\n        {\n            "page_id": page.page_id,\n            "pdf_page_number_for_audit": page.page_number,\n            "text_layer": page.text[:12000],\n        }\n        for page in pages\n    ]\n    return json.dumps({\n        "page_ledger": ledger,\n        "instruction": (\n            "Correct only the stated structural defect and return the complete "\n            "page batch. Preserve all source-visible content."\n        ),\n        "validation_failure": str(reason or "unspecified validation failure")[:4000],\n        "candidate": candidate,\n    }, ensure_ascii=False)\n\n\n'''
if verification_marker not in text:
    raise SystemExit('correction prompt insertion marker not found')
text = text.replace(verification_marker, correction_helpers + verification_marker, 1)

validation_marker = '''def validate_page_extraction(\n    pages: list[PdfPage],\n    candidate: dict[str, Any],\n) -> tuple[dict[str, Any] | None, str]:\n'''
source_normalizer = '''def _canonicalize_source_cue_block(raw: dict[str, Any]) -> dict[str, Any]:\n    """Normalize the model's historical-source cue into an explicit source block.\n\n    The original schema allowed source_label on every block even though the\n    validator reserved it for tasks. Source-heavy textbooks therefore produced\n    a valid JSON object that the deterministic contract could never accept. This\n    normalization is semantic-field reconciliation, not content inference: the\n    exact label, text, bbox, order, and confidence are retained.\n    """\n    block = copy.deepcopy(raw)\n    kind = str(block.get("kind") or "")\n    source_label = str(block.get("source_label") or "").strip()\n    if source_label and kind in _SOURCE_COMPATIBLE_KINDS:\n        block["kind"] = "source"\n        block["heading_level"] = 0\n    elif kind == "source":\n        block["heading_level"] = 0\n    return block\n\n\n'''
if validation_marker not in text:
    raise SystemExit('source normalizer insertion marker not found')
text = text.replace(validation_marker, source_normalizer + validation_marker, 1)

replace_once(
    '''        for raw in blocks:\n            if not isinstance(raw, dict):\n                return None, f"{page_id} contains a non-object block"\n            order = int(raw.get("reading_order") or 0)\n''',
    '''        for raw in blocks:\n            if not isinstance(raw, dict):\n                return None, f"{page_id} contains a non-object block"\n            raw = _canonicalize_source_cue_block(raw)\n            order = int(raw.get("reading_order") or 0)\n''',
    'source normalization call',
)
replace_once(
    '''            if kind not in {"figure", "math", "table"} and not text:\n                return None, f"{page_id} block {order} has no visible text"\n''',
    '''            if kind not in {"figure", "math", "table", "source"} and not text:\n                return None, f"{page_id} block {order} has no visible text"\n            if kind == "source" and not (source_label or text):\n                return None, f"{page_id} source block {order} has no visible content"\n''',
    'source visible content validation',
)
replace_once(
    '''            if kind == "task" and not source_label:\n                return None, f"{page_id} task block {order} has no source cue"\n            if kind != "task" and source_label:\n                return None, f"{page_id} non-task block {order} has a source cue"\n''',
    '''            if kind == "task" and not source_label:\n                return None, f"{page_id} task block {order} has no source cue"\n            if kind == "source" and not source_label:\n                return None, f"{page_id} source block {order} has no source cue"\n            if kind not in {"task", "source"} and source_label:\n                return None, f"{page_id} non-task/non-source block {order} has a source cue"\n''',
    'source label validation',
)

start = text.index('def extract_batch_via_openai(pages: list[PdfPage]) -> dict[str, Any]:\n')
end = text.index('\n\ndef extract_pdf_to_page_acsd(', start)
replacement_function = '''def _verification_rejection_reason(\n    pages: list[PdfPage],\n    verification: dict[str, Any],\n) -> str:\n    verdict = str(verification.get("verdict") or "")\n    approved = sorted(str(value) for value in verification.get("approved_page_ids") or [])\n    expected = sorted(page.page_id for page in pages)\n    confidence = float(verification.get("confidence") or 0.0)\n    rejected = sorted(str(value) for value in verification.get("rejected_page_ids") or [])\n    issues = [\n        str(value).strip() for value in verification.get("issues") or []\n        if str(value).strip()\n    ]\n    reasons = list(issues)\n    if verdict != "verified":\n        reasons.append(f"verification verdict was {verdict or 'missing'}")\n    if approved != expected:\n        reasons.append("verification did not approve every supplied page exactly once")\n    if rejected:\n        reasons.append(f"verification rejected page(s): {', '.join(rejected)}")\n    if confidence < _min_page_confidence():\n        reasons.append(\n            f"verification confidence {confidence:.3f} is below "\n            f"{_min_page_confidence():.3f}"\n        )\n    return "; ".join(dict.fromkeys(reasons))\n\n\ndef extract_batch_via_openai(pages: list[PdfPage]) -> dict[str, Any]:\n    evidence_pages = [\n        phase22.EvidencePage(\n            evidence_id=page.page_id,\n            page_number=page.page_number,\n            text=page.text,\n            image_data_url=page.image_data_url,\n            score=1.0,\n        )\n        for page in pages\n    ]\n    candidate = phase22._openai_multimodal_json(\n        system=_extraction_system_prompt(),\n        prompt=_page_prompt(pages),\n        pages=evidence_pages,\n        response_schema=extraction_schema(pages),\n        purpose="source_adjudication",\n        max_tokens=_max_output_tokens(),\n    )\n    correction_history: list[dict[str, Any]] = []\n    max_corrections = _max_correction_attempts()\n    verification: dict[str, Any] = {}\n\n    for pass_index in range(max_corrections + 1):\n        normalized, reason = validate_page_extraction(pages, candidate)\n        if normalized is None:\n            if pass_index >= max_corrections:\n                return {\n                    "status": "review_required",\n                    "reason": reason,\n                    "correction_history": correction_history,\n                }\n            attempt = pass_index + 1\n            correction_history.append({\n                "attempt": attempt,\n                "stage": "deterministic_validation",\n                "reason": reason,\n            })\n            progress.log(\n                f"GPT PDF-to-ACSD bounded correction {attempt}/{max_corrections}: "\n                f"{reason}",\n                level="warning",\n            )\n            candidate = phase22._openai_multimodal_json(\n                system=_correction_system_prompt(),\n                prompt=_correction_prompt(\n                    pages, candidate=candidate, reason=reason\n                ),\n                pages=evidence_pages,\n                response_schema=extraction_schema(pages),\n                purpose="source_adjudication",\n                max_tokens=_max_output_tokens(),\n            )\n            continue\n\n        verification = phase22._openai_multimodal_json(\n            system=_verification_system_prompt(),\n            prompt=_page_prompt(pages, candidate=normalized),\n            pages=evidence_pages,\n            response_schema=verification_schema(pages),\n            purpose="source_adjudication",\n            max_tokens=6000,\n        )\n        reason = _verification_rejection_reason(pages, verification)\n        if not reason:\n            return {\n                "status": "verified",\n                "pages": normalized["pages"],\n                "verification": verification,\n                "correction_history": correction_history,\n            }\n        if pass_index >= max_corrections:\n            return {\n                "status": "review_required",\n                "reason": reason,\n                "verification": verification,\n                "correction_history": correction_history,\n            }\n\n        attempt = pass_index + 1\n        correction_history.append({\n            "attempt": attempt,\n            "stage": "independent_verification",\n            "reason": reason,\n        })\n        progress.log(\n            f"GPT PDF-to-ACSD bounded correction {attempt}/{max_corrections}: "\n            f"{reason}",\n            level="warning",\n        )\n        candidate = phase22._openai_multimodal_json(\n            system=_correction_system_prompt(),\n            prompt=_correction_prompt(\n                pages, candidate=normalized, reason=reason\n            ),\n            pages=evidence_pages,\n            response_schema=extraction_schema(pages),\n            purpose="source_adjudication",\n            max_tokens=_max_output_tokens(),\n        )\n\n    return {\n        "status": "review_required",\n        "reason": "bounded correction loop ended without a verified candidate",\n        "verification": verification,\n        "correction_history": correction_history,\n    }\n'''
text = text[:start] + replacement_function + text[end:]

replace_once(
    '''            elif kind == "heading":\n                parts.append(_markdown_heading(\n                    int(block.get("heading_level") or 1), text\n                ))\n            elif kind == "task":\n''',
    '''            elif kind == "heading":\n                parts.append(_markdown_heading(\n                    int(block.get("heading_level") or 1), text\n                ))\n            elif kind == "source":\n                label = str(block.get("source_label") or "").strip()\n                label_key = _normal(label)\n                text_key = _normal(text)\n                text_already_contains_label = bool(\n                    label_key and (\n                        text_key == label_key\n                        or text_key.startswith(label_key + " ")\n                    )\n                )\n                if label and not text_already_contains_label:\n                    parts.append(_markdown_heading(1, label))\n                if text:\n                    parts.append(text)\n            elif kind == "task":\n''',
    'source renderer',
)
replace_once(
    '''    if kind == "math":\n        latex = str(block.get("latex") or "").strip()\n        return kr.katex(latex) if latex else ""\n    return str(block.get("text") or "").strip()\n''',
    '''    if kind == "math":\n        latex = str(block.get("latex") or "").strip()\n        return kr.katex(latex) if latex else ""\n    if kind == "source":\n        label = str(block.get("source_label") or "").strip()\n        text = str(block.get("text") or "").strip()\n        label_key = _normal(label)\n        text_key = _normal(text)\n        if label and not (\n            text_key == label_key or text_key.startswith(label_key + " ")\n        ):\n            return "\\n".join(value for value in (label, text) if value)\n        return text or label\n    return str(block.get("text") or "").strip()\n''',
    'source context text',
)
replace_once(
    'allowed_kinds={"paragraph", "list", "table", "math", "other"},',
    'allowed_kinds={"paragraph", "source", "list", "table", "math", "other"},',
    'source context matching',
)

target.write_text(text, encoding='utf-8')
