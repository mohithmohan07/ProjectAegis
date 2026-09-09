"""Parse/render every declared equation with the target KaTeX engine.

This is a mechanical render check, never mathematical or semantic judgment.
It produces a frozen report; it does not mutate, repair or discard content.
Node is invoked only by explicit inspection, never by report/readiness readers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from collections.abc import Mapping
from typing import Any

VERSION = "katex-render-validation-1"
KATEX_VERSION = "0.18.7"
REPORT_FIELD = "katex_render_validation"
MAX_EXPRESSIONS = 10000
MAX_EXPRESSION_CHARS = 32768
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
NODE_TIMEOUT_SECONDS = 20

_SPAN = re.compile(r"\[katex\](.*?)\[/katex\]", re.I | re.S)
_TOKEN = re.compile(r"\[/?katex\]", re.I)
_ANSWER_TYPE = re.compile(r"answer_type_(\d+)\Z")
_CHILD_TYPE = re.compile(r"sq(\d+)_answer_type_(\d+)\Z")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _issue(code: str, message: str, **context: Any) -> dict[str, Any]:
    return {"code": code, "phase": "render", "severity": "error",
            "message": message, **context}


def _equation_fields(record: Mapping) -> set[str]:
    """Follow declared schema types; do not infer mathematics from prose."""
    fields: set[str] = set()
    for key, value in record.items():
        if not isinstance(key, str) or str(value).strip().lower() != "equation":
            continue
        if key == "answer_type":
            fields.update(name for name in ("answer_content", "keyword", "answer")
                          if name in record)
        elif match := _ANSWER_TYPE.fullmatch(key):
            fields.update(name for name in (
                f"answer_content_{match[1]}", f"answer_{match[1]}",
            ) if name in record)
        elif match := _CHILD_TYPE.fullmatch(key):
            fields.add(f"sq{match[1]}_keyword_{match[2]}")
    return fields


def _collect(value: Any) -> tuple[dict[str, dict], list[dict]]:
    expressions: dict[str, dict] = {}
    findings: list[dict] = []

    def add(latex: str, path: str, medium: str) -> None:
        if not latex.strip():
            findings.append(_issue("katex_empty_expression", "Empty equation", path=path))
            return
        identity = hashlib.sha256(latex.encode("utf-8")).hexdigest()
        entry = expressions.setdefault(identity, {"id": identity, "latex": latex,
                                                   "occurrences": []})
        entry["occurrences"].append({"path": path, "medium": medium})

    def text(value: str, path: str) -> None:
        spans = list(_SPAN.finditer(value))
        if len(list(_TOKEN.finditer(value))) != len(spans) * 2:
            findings.append(_issue("katex_wrapper_syntax", "Unbalanced KaTeX wrappers", path=path))
        for match in spans:
            if not match[0].startswith("[Katex]") or not match[0].endswith("[/Katex]"):
                findings.append(_issue("katex_wrapper_syntax", "Use exact [Katex] wrappers", path=path))
            add(match[1], path, "wrapped")

    def walk(item: Any, path: str) -> None:
        if isinstance(item, Mapping):
            equation_fields = _equation_fields(item)
            for field in sorted(equation_fields):
                raw = item.get(field)
                target = f"{path}.{field}"
                if not isinstance(raw, str):
                    findings.append(_issue("katex_equation_type", "Equation cell must contain raw LaTeX text", path=target))
                elif _TOKEN.search(raw):
                    findings.append(_issue("katex_equation_wrapped", "Typed Equation cell must contain raw LaTeX without wrappers", path=target))
                else:
                    add(raw, target, "Equation")
            for key in sorted(item, key=str):
                if str(key).startswith("_"):
                    continue  # Private audit fields are not serialized CMS cells.
                child = item[key]
                walk(child, f"{path}.{key}")
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                walk(child, f"{path}[{index}]")
        elif isinstance(item, str):
            text(item, path)

    walk(value, "$")
    return expressions, findings


def _render_value(value: Any) -> Any:
    """Match concept staging's public projection; exclude audit/source proofs."""
    if not isinstance(value, Mapping) or set(value) != {"records", "chapter_meta"}:
        return value
    records = []
    raw_records = value.get("records") or []
    if not isinstance(raw_records, (list, tuple)):
        raw_records = []
    for row in raw_records:
        if not isinstance(row, Mapping):
            continue  # The structural gate separately reports malformed rows.
        records.append({
            "topic": row.get("topic") or "",
            "concept_title": row.get("concept_title") or row.get("concept") or "",
            "concept_details": row.get("concept_details") or row.get("concept_description") or "",
            **{field: row.get(field) or "" for field in (
                "parent_concept", "keywords", "related_concepts", "digicards",
            )},
        })
    metadata = value.get("chapter_meta") or {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    return {"records": records, "chapter_meta": {
        key: metadata.get(key) or "" for key in (
            "chapter_description", "topic_descriptions",
        )
    }}


def _binding(expressions: Mapping[str, dict], findings: list[dict]) -> str:
    return _hash({"expressions": list(expressions.values()), "syntax_findings": findings})


def _runtime() -> tuple[str, Path]:
    node = os.environ.get("AEGIS_KATEX_NODE") or shutil.which("node") or ""
    script = Path(os.environ.get("AEGIS_KATEX_SCRIPT") or (
        Path(__file__).resolve().parents[3] / "frontend/scripts/validate-katex.mjs"
    ))
    if not node or not script.is_file():
        raise FileNotFoundError("Target KaTeX Node runtime or validator script is unavailable")
    return node, script


def inspect_render(value: Any) -> dict[str, Any]:
    """Inspect structured final fields and return a content-bound render receipt."""
    expressions, findings = _collect(_render_value(value))
    report: dict[str, Any] = {
        "version": VERSION, "expected_engine_version": KATEX_VERSION,
        "input_sha256": _binding(expressions, findings),
        "input_binding": "exported_expression_bytes_and_locations",
        "status": "blocked" if findings else "pass",
        "expression_count": len(expressions),
        "occurrence_count": sum(len(v["occurrences"]) for v in expressions.values()),
        "checked_expression_count": 0, "engine_version": None,
        "findings": findings, "expressions": [],
    }
    if not expressions:
        return report
    request = json.dumps({
        "expected_version": KATEX_VERSION,
        "expressions": [{"id": v["id"], "latex": v["latex"]} for v in expressions.values()],
    }, ensure_ascii=False).encode("utf-8")
    if (len(expressions) > MAX_EXPRESSIONS or len(request) > MAX_INPUT_BYTES
            or any(len(v["latex"]) > MAX_EXPRESSION_CHARS for v in expressions.values())):
        report["status"] = "blocked"
        findings.append(_issue("katex_render_resource_limit", "Complete render validation exceeds the bounded request; no partial pass was issued"))
        return report
    try:
        node, script = _runtime()
        process = subprocess.run(
            [node, "--max-old-space-size=256", str(script)], input=request,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            timeout=NODE_TIMEOUT_SECONDS,
        )
        if process.returncode or len(process.stdout) > MAX_OUTPUT_BYTES:
            raise ValueError("KaTeX validator did not return a bounded successful response")
        response = json.loads(process.stdout)
        if response.get("status") != "checked" or response.get("version") != KATEX_VERSION:
            raise ValueError(response.get("message") or "KaTeX engine version does not match the target")
        results = response.get("results")
        if not isinstance(results, list) or len(results) != len(expressions):
            raise ValueError("KaTeX validator did not check every unique expression")
        seen: set[str] = set()
        for result in results:
            identity = result.get("id")
            if identity not in expressions or identity in seen or not isinstance(result.get("ok"), bool):
                raise ValueError("KaTeX validator returned mismatched expression receipts")
            seen.add(identity)
        report["engine_version"] = response["version"]
        report["checked_expression_count"] = len(results)
        for result in results:
            entry = expressions[result["id"]]
            receipt = {**result, "occurrences": entry["occurrences"]}
            report["expressions"].append(receipt)
            if not result["ok"]:
                findings.append(_issue(
                    result.get("code") or "katex_render_invalid",
                    result.get("message") or "Equation failed target rendering",
                    expression_sha256=result["id"], occurrences=entry["occurrences"],
                ))
        report["status"] = "blocked" if findings else "pass"
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired) as exc:
        report["status"] = "unavailable"
        findings.append(_issue("katex_renderer_unavailable", str(exc)[:1200]))
    return report


def validate_workbooks(workbooks: Mapping[str, bytes]) -> dict[str, Any]:
    """Read the actual serialized XLSX cells, including every typed Equation."""
    from ..bulk_import.assessment_workbook import parse_workbook

    values: dict[str, Any] = {}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in workbooks.items()}
    try:
        for name, data in workbooks.items():
            parsed = parse_workbook(data)
            values[name] = {
                sheet: {str(number): row for number, row in zip(
                    info["row_numbers"], info["rows"], strict=True,
                )} for sheet, info in parsed["sheets"].items()
            }
    except Exception as exc:
        return {"version": VERSION, "status": "blocked", "workbook_sha256s": hashes,
                "findings": [_issue("katex_workbook_readback_failed", str(exc)[:1200])]}
    report = inspect_render(values)
    report["workbook_sha256s"] = hashes
    report["scope"] = "serialized_workbook_cells"
    return report


def release_findings(report: Mapping[str, Any]) -> list[dict]:
    return [dict(item) for item in report.get("findings", []) if isinstance(item, Mapping)]


def report_defects(report: Any) -> list[str]:
    """Pure reader: fail visibly when the target engine receipt is incomplete."""
    if not isinstance(report, Mapping) or report.get("version") != VERSION:
        return ["katex_render_report_missing: target KaTeX validation is not recorded"]
    if report.get("status") != "pass":
        findings = release_findings(report)
        return [f"{v.get('code', 'katex_render_failed')}: {v.get('message', '')}" for v in findings] or ["katex_render_failed: target rendering is not verified"]
    count = report.get("expression_count")
    receipts = report.get("expressions")
    if (type(count) is not int or count < 0
            or count != report.get("checked_expression_count")
            or report.get("expected_engine_version") != KATEX_VERSION
            or not isinstance(receipts, list)
            or len(receipts) != count
            or any(not isinstance(row, Mapping) or row.get("ok") is not True
                   for row in receipts)
            or report.get("findings")
            or (count and report.get("engine_version") != KATEX_VERSION)):
        return ["katex_render_incomplete: target engine has not checked every expression"]
    return []


def readiness_defects(payload: Mapping[str, Any]) -> list[str]:
    """Pure reader bound to the exact staged records and chapter metadata."""
    report = payload.get(REPORT_FIELD)
    defects = report_defects(report)
    if isinstance(report, Mapping):
        value = {"records": payload.get("records") or [],
                 "chapter_meta": payload.get("chapter_meta") or {}}
        expressions, findings = _collect(_render_value(value))
        if report.get("input_sha256") != _binding(expressions, findings):
            defects.append("katex_render_stale: staged fields changed after target render validation")
    return defects
