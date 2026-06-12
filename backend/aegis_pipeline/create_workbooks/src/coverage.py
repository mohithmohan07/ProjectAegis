"""Verify planner/builder honour the coverage contract (plan vs build JSON)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _blob_from_raw(raw: dict) -> str:
    parts = [json.dumps(raw.get("glossary", []), ensure_ascii=False)]
    for topic in raw.get("topics") or []:
        parts.append(json.dumps(topic, ensure_ascii=False))
    return _norm(" ".join(parts))


def _glossary_terms(raw: dict) -> set[str]:
    gloss = raw.get("glossary") or []
    if isinstance(gloss, dict):
        return {_norm(k) for k in gloss if str(k).strip()}
    terms: set[str] = set()
    for item in gloss:
        if isinstance(item, dict):
            term = str(item.get("term", "")).strip()
            if term:
                terms.add(_norm(term))
    return terms


def _count_plan_by_topic(plan: dict, inventory_key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in plan.get(inventory_key) or []:
        num = str(item.get("topic_number", "")).zfill(2)
        counts[num] = counts.get(num, 0) + 1
    return counts


def _count_built_problems(raw: dict) -> dict[str, int]:
    counts: dict[str, int] = {}

    def _add_from_block(num: str, block: dict) -> None:
        btype = block.get("type")
        if btype == "worked_example":
            counts[num] = counts.get(num, 0) + 1
        elif btype == "problem_set":
            counts[num] = counts.get(num, 0) + len((block.get("data") or {}).get("problems") or [])
        elif "problem_set" in block and isinstance(block["problem_set"], dict):
            counts[num] = counts.get(num, 0) + len(block["problem_set"].get("problems") or [])

    for topic in raw.get("topics") or []:
        num = str(topic.get("number", "")).zfill(2)
        for block in topic.get("blocks") or []:
            if isinstance(block, dict):
                _add_from_block(num, block)
        for ps in topic.get("problem_set") or []:
            if isinstance(ps, dict):
                counts[num] = counts.get(num, 0) + len(ps.get("problems") or [])
    return counts


def _count_built_activities(raw: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for topic in raw.get("topics") or []:
        num = str(topic.get("number", "")).zfill(2)
        counts[num] = len(topic.get("activities") or [])
    return counts


def _glossary_term_present(term: str, gloss_built: set[str], blob: str) -> bool:
    key = _norm(term)
    if key in gloss_built:
        return True
    if key in blob:
        return True
    # Allow minor planner typos (e.g. civlisation vs civilisation).
    for built in gloss_built:
        if key in built or built in key:
            return True
    return False


@dataclass
class CoverageReport:
    stem: str
    plan_topics: int = 0
    built_topics: int = 0
    plan_problems: int = 0
    plan_activities: int = 0
    plan_excerpts: int = 0
    plan_cases: int = 0
    plan_glossary: int = 0
    built_glossary: int = 0
    missing_build: bool = False
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_build and not self.issues


def _numerical_item_covered(statement: str, blob: str) -> bool:
    """True when a planner numerical statement is reflected in the build."""
    stmt = _norm(statement)
    if not stmt or stmt in blob:
        return True

    all_nums = re.findall(r"\d+\.?\d*", statement)
    distinctive: list[str] = []
    answer_nums: list[str] = []
    if "=" in statement:
        answer_nums = re.findall(r"\d+\.?\d*", statement.split("=")[-1])
    for n in all_nums:
        try:
            v = float(n)
        except ValueError:
            continue
        if n in answer_nums or v >= 15 or (v != int(v)) or n in {"0.2", "0.15", "1.2", "1.67", "20", "40"}:
            if n not in distinctive:
                distinctive.append(n)
    if distinctive:
        blob_flat = blob.replace(" ", "")
        hits = sum(
            1 for n in distinctive
            if n in blob or n in blob_flat or n.replace(".", "") in blob_flat
        )
        if hits >= max(1, len(distinctive) // 2):
            return True
        # Distinctive numbers listed but not found — not covered.
        return False

    # Unit conversions: both units mentioned somewhere in the chapter build.
    low = stmt
    if "nanometre" in low or "nanometer" in low:
        if ("nanometre" in blob or "nanometer" in blob or " nm" in blob) and "mm" in blob:
            return True

    tokens = [
        t for t in re.findall(r"[a-z]{4,}", low)
        if t not in {
            "that", "with", "from", "when", "which", "about", "assume", "vehicle",
            "system", "while", "driver", "reversing", "fitted", "distance", "sensor",
            "assistance", "provides", "echolocation", "obstacle", "warning", "starts",
            "sounding", "travel", "speed", "ultrasonic", "wave", "air", "part", "parking",
            "emits", "reflected", "beep", "taken", "come", "back",
        }
    ]
    if tokens:
        hits = sum(1 for t in tokens if t in blob)
        return hits >= max(2, len(tokens) // 2)
    return False


def audit_plan_vs_build(stem: str, plan: dict, raw: dict | None) -> CoverageReport:
    report = CoverageReport(stem=stem)
    report.plan_topics = len(plan.get("topics") or [])
    report.plan_glossary = len(plan.get("glossary_terms") or [])
    report.plan_problems = len(plan.get("problem_inventory") or [])
    plan_prob_key = "problem_inventory"
    if "numerical_inventory" in plan:
        report.plan_problems = len(plan.get("numerical_inventory") or [])
        plan_prob_key = "numerical_inventory"
    report.plan_activities = len(plan.get("activity_inventory") or [])
    report.plan_excerpts = len(plan.get("excerpt_inventory") or [])
    report.plan_cases = len(plan.get("case_inventory") or [])

    if raw is None:
        report.missing_build = True
        report.issues.append("No build.raw.json — chapter was never built or build failed.")
        return report

    report.built_topics = len(raw.get("topics") or [])
    report.built_glossary = len(_glossary_terms(raw))
    blob = _blob_from_raw(raw)

    if report.plan_topics != report.built_topics:
        report.issues.append(
            f"Topic count mismatch: plan={report.plan_topics}, built={report.built_topics}"
        )

    plan_nums = {str(t.get("number", "")).zfill(2) for t in plan.get("topics") or []}
    built_nums = {str(t.get("number", "")).zfill(2) for t in raw.get("topics") or []}
    missing_topics = sorted(plan_nums - built_nums)
    if missing_topics:
        report.issues.append(f"Missing topic numbers: {', '.join(missing_topics)}")

    extra_topics = sorted(built_nums - plan_nums)
    if extra_topics:
        report.issues.append(f"Unexpected extra topic numbers: {', '.join(extra_topics)}")

    gloss_plan = plan.get("glossary_terms") or []
    gloss_built = _glossary_terms(raw)
    missing_gloss = [t for t in gloss_plan if not _glossary_term_present(t, gloss_built, blob)]
    if missing_gloss:
        preview = ", ".join(missing_gloss[:5])
        suffix = "…" if len(missing_gloss) > 5 else ""
        report.issues.append(
            f"Glossary terms missing ({len(missing_gloss)}/{len(gloss_plan)}): {preview}{suffix}"
        )

    plan_probs = _count_plan_by_topic(plan, plan_prob_key)
    built_probs = _count_built_problems(raw)
    prob_shortfalls: list[str] = []
    if plan_prob_key == "numerical_inventory":
        # Science often merges several inventory numericals into one worked example.
        missing_nums: list[str] = []
        for item in plan.get("numerical_inventory") or []:
            stmt = str(item.get("statement", "")).strip()
            eid = str(item.get("id", "")).strip()
            if stmt and not _numerical_item_covered(stmt, blob):
                missing_nums.append(eid or stmt[:35])
        if missing_nums:
            preview = ", ".join(missing_nums[:6])
            suffix = "…" if len(missing_nums) > 6 else ""
            report.issues.append(
                f"Numerical items not reflected in build ({len(missing_nums)} missing): "
                f"{preview}{suffix}"
            )
    else:
        for num, planned in sorted(plan_probs.items()):
            built = built_probs.get(num, 0)
            if built < planned:
                prob_shortfalls.append(f"T{num}: {built}/{planned}")
        if prob_shortfalls:
            plan_total = sum(plan_probs.values())
            built_total = sum(built_probs.values())
            preview = ", ".join(prob_shortfalls[:6])
            suffix = "…" if len(prob_shortfalls) > 6 else ""
            report.issues.append(
                f"Problem shortfall by topic ({built_total}/{plan_total} total): {preview}{suffix}"
            )

    plan_acts = _count_plan_by_topic(plan, "activity_inventory")
    built_acts = _count_built_activities(raw)
    act_shortfalls: list[str] = []
    for num, planned in sorted(plan_acts.items()):
        built = built_acts.get(num, 0)
        if built < planned:
            act_shortfalls.append(f"T{num}: {built}/{planned}")
    if act_shortfalls:
        report.issues.append(
            "Activity shortfall by topic: " + ", ".join(act_shortfalls)
        )

    for exc in plan.get("excerpt_inventory") or []:
        text = str(exc.get("text", "")).strip()
        eid = str(exc.get("id", "")).strip()
        key = _norm(text)[:80]
        if key and key not in blob:
            # Allow partial match for long excerpts.
            words = [w for w in re.findall(r"[a-z]{4,}", key)[:8]]
            if words and not all(w in blob for w in words[:4]):
                report.issues.append(f"Excerpt missing: {eid or text[:40]}")

    for case in plan.get("case_inventory") or []:
        cid = str(case.get("id", "")).strip()
        title = str(case.get("title", "")).strip()
        facts = case.get("facts") or []
        found = _norm(title) in blob or (cid and _norm(cid) in blob)
        if not found and facts:
            found = sum(1 for f in facts if _norm(str(f))[:35] in blob) >= max(1, len(facts) // 2)
        if not found:
            report.issues.append(f"Case study missing: {cid or title}")

    return report


def audit_chapter_files(plan_path: Path, raw_path: Path | None = None) -> CoverageReport:
    stem = plan_path.stem.replace(".plan", "")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    raw: dict | None = None
    if raw_path is None:
        raw_path = plan_path.with_name(f"{stem}.build.raw.json")
    if raw_path.exists():
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report = audit_plan_vs_build(stem, plan, None)
            report.issues.append("build.raw.json is invalid JSON")
            return report
    return audit_plan_vs_build(stem, plan, raw)


def audit_all(plan_cache_dir: str | Path) -> list[CoverageReport]:
    root = Path(plan_cache_dir)
    reports: list[CoverageReport] = []
    for plan_path in sorted(root.glob("*.plan.json")):
        reports.append(audit_chapter_files(plan_path))
    return reports


def format_report_table(reports: list[CoverageReport]) -> str:
    lines = [
        f"{'Chapter':<62} {'Plan':>4} {'Built':>5} {'Prob':>4} {'Status':>8}",
        "-" * 90,
    ]
    for r in reports:
        status = "OK" if r.ok else "FAIL"
        if r.missing_build:
            status = "NO BUILD"
        short = r.stem[:62]
        lines.append(
            f"{short:<62} {r.plan_topics:>4} {r.built_topics:>5} "
            f"{r.plan_problems:>4} {status:>8}"
        )
    return "\n".join(lines)


def format_report_details(reports: list[CoverageReport]) -> str:
    chunks: list[str] = [format_report_table(reports), ""]
    failed = [r for r in reports if not r.ok]
    if not failed:
        chunks.append("All chapters pass coverage checks.")
        return "\n".join(chunks)

    chunks.append(f"FAILURES ({len(failed)}):")
    for r in failed:
        chunks.append(f"\n=== {r.stem} ===")
        for issue in r.issues:
            chunks.append(f"  - {issue}")
    return "\n".join(chunks)
