"""Stage registry + runner that wraps the vendored Aegis pipeline scripts.

Each stage has a ``dry`` mode that produces realistic dummy artifacts without
needing API keys, and a ``live`` mode that delegates to the vendored script.
Live mode is only enabled when the relevant environment variables are present.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import DATA_DIR

ARTIFACT_ROOT = DATA_DIR / "artifacts"
ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class Stage:
    key: str
    title: str
    order: int
    description: str
    inputs: list[str]
    outputs: list[str]
    dependencies: list[str]
    requires_keys: list[str]
    dry: Callable[[Path, dict[str, Any]], dict[str, Any]]
    live: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None


def _has_keys(keys: list[str]) -> bool:
    return all(os.environ.get(k) for k in keys)


# --------------------------------------------------------------------------- #
# Dry-mode stage implementations
# --------------------------------------------------------------------------- #

def _dry_extract_pdfs(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    chapter_codes = inp.get("chapter_codes") or ["09ICMA_CH01", "09ICMA_CH02"]
    manifest = []
    for code in chapter_codes:
        pdf_name = f"{code}.pdf"
        (out / pdf_name).write_bytes(b"%PDF-1.4\n%dummy aegis fixture\n%%EOF\n")
        manifest.append({
            "chapter_code": code,
            "drive_ids": [f"drive-stub-{code}"],
            "local_pdf_path": str(out / pdf_name),
            "status": "PDF_DOWNLOADED",
        })
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return {"manifest": manifest, "pdf_count": len(manifest)}


def _dry_extract_mmds(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    chapter_codes = inp.get("chapter_codes") or ["09ICMA_CH01", "09ICMA_CH02"]
    written = []
    for code in chapter_codes:
        path = out / f"{code}.mmd"
        path.write_text(
            f"# {code}\n\n"
            "## Topic 1: Overview\n"
            "Concept A: Definition of the concept...\n\n"
            "Concept B: Worked example with $$x^2 + y^2 = z^2$$\n\n"
            "## Topic 2: Applications\n"
            "Concept C: Real-world application of the topic.\n"
        )
        written.append(str(path))
    return {"mmd_files": written, "count": len(written)}


def _dry_mmd_to_concepts(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    rows = inp.get("rows") or _dummy_concept_rows()
    df = pd.DataFrame(rows)
    xlsx = out / "concepts.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Concepts")
    return {"concepts_xlsx": str(xlsx), "concept_count": len(df)}


def _dry_excel_to_prelearning(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    rows = inp.get("rows") or _dummy_concept_rows(prefix="PL_")
    for r in rows:
        # Concept Description gets the // Types // Misconception suffix added.
        if "//" not in r["Concept Description"]:
            r["Concept Description"] = (
                f"{r['Concept Description']} // Types: Type 01: Standard "
                "// Misconception: students often confuse this with related ideas."
            )
    df = pd.DataFrame(rows)
    xlsx = out / "pre_learning_concepts.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Concepts")
    return {"pre_learning_xlsx": str(xlsx), "row_count": len(df)}


def _dry_concept_mapping_to_prelearning(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    return _dry_excel_to_prelearning(out, inp)


def _dry_bulk_upload(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    objective = pd.DataFrame([_dummy_question_row("objective", i) for i in range(3)])
    subjective = pd.DataFrame([_dummy_question_row("subjective", i) for i in range(2)])
    descriptive = pd.DataFrame([_dummy_question_row("descriptive", i) for i in range(2)])
    xlsx = out / "final_output.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        objective.to_excel(w, index=False, sheet_name="Objective")
        subjective.to_excel(w, index=False, sheet_name="Subjective")
        descriptive.to_excel(w, index=False, sheet_name="Descriptive")
    return {
        "bulk_upload_xlsx": str(xlsx),
        "counts": {"objective": len(objective), "subjective": len(subjective), "descriptive": len(descriptive)},
    }


def _dry_assessment_tagging(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    chapter = inp.get("chapter_code", "09ICMA_CH01")
    decisions = [
        {"question_label": f"{chapter}_Q{i:02d}", "concept": f"Concept {chr(64+i)}",
         "topic": f"Topic {1 + (i % 2)}", "group_type": ["Basic", "Intermediate", "Advanced"][i % 3],
         "comment": "" if i % 4 else "cross_topic"}
        for i in range(1, 7)
    ]
    (out / "tagging_decisions.json").write_text(json.dumps(decisions, indent=2))
    return {"chapter_code": chapter, "decisions": decisions, "count": len(decisions)}


def _dummy_concept_rows(prefix: str = "") -> list[dict[str, Any]]:
    return [
        {
            "Board": "ICSE", "Book": "SE", "Grade": "09", "Subject": "Mathematics",
            "Chapter No": "01", "Chapter Code": "09ICMA_CH01", "Chapter Title": "Number Systems",
            "Topic": "Topic 01: Real Numbers",
            "Parent Concept": "",
            "Concept": f"{prefix}Rational Numbers",
            "Concept Description": "A rational number can be expressed as p/q where q != 0.",
            "Concept ID": "09ICMA_CH01-C001",
            "MMD Path": "data/mmds/09ICMA_CH01.mmd",
            "PDF Path": "data/pdfs/09ICMA_CH01.pdf",
        },
        {
            "Board": "ICSE", "Book": "SE", "Grade": "09", "Subject": "Mathematics",
            "Chapter No": "01", "Chapter Code": "09ICMA_CH01", "Chapter Title": "Number Systems",
            "Topic": "Topic 01: Real Numbers",
            "Parent Concept": f"{prefix}Rational Numbers",
            "Concept": f"{prefix}Irrational Numbers",
            "Concept Description": "A real number that cannot be expressed as p/q.",
            "Concept ID": "09ICMA_CH01-C002",
            "MMD Path": "data/mmds/09ICMA_CH01.mmd",
            "PDF Path": "data/pdfs/09ICMA_CH01.pdf",
        },
        {
            "Board": "ICSE", "Book": "SE", "Grade": "09", "Subject": "Physics",
            "Chapter No": "03", "Chapter Code": "09ICPH_CH03", "Chapter Title": "Laws of Motion",
            "Topic": "Topic 02: Newton's Laws",
            "Parent Concept": "",
            "Concept": f"{prefix}Newton's Third Law",
            "Concept Description": "Every action has an equal and opposite reaction.",
            "Concept ID": "09ICPH_CH03-C005",
            "MMD Path": "data/mmds/09ICPH_CH03.mmd",
            "PDF Path": "data/pdfs/09ICPH_CH03.pdf",
        },
    ]


def _dummy_question_row(kind: str, idx: int) -> dict[str, Any]:
    base = {
        "Question Label": f"09ICMA_CH01_PL_Q{idx+1:02d}",
        "Question Category": (
            "Multiple Choice Question" if kind == "objective"
            else "Short Answer (3 marks)" if kind == "subjective"
            else "Long Answer (5 marks)"
        ),
        "Cognitive Skills": ["Remembering", "Understanding", "Applying"][idx % 3],
        "Question Source": "ICSE Past Paper 2024",
        "Question Appears in": "Chapter 1 - Number Systems",
        "Level of Difficulty": ["Less", "Moderate", "High"][idx % 3],
        "Question": f"Sample {kind} question {idx+1} about rational numbers.",
        "Marks": 1 if kind == "objective" else 3 if kind == "subjective" else 5,
    }
    if kind == "objective":
        base.update({
            "Answer Type1": "Phrases", "Answer Content1": "Yes", "Correct Answer1": "TRUE", "Answer Weightage1": 1,
            "Answer Type2": "Phrases", "Answer Content2": "No", "Correct Answer2": "FALSE", "Answer Weightage2": 0,
            "Answer Type3": "Phrases", "Answer Content3": "Maybe", "Correct Answer3": "FALSE", "Answer Weightage3": 0,
            "Answer Type4": "Phrases", "Answer Content4": "Cannot say", "Correct Answer4": "FALSE", "Answer Weightage4": 0,
            "Answer Explanation": "Because the definition implies the property.",
        })
    else:
        base.update({
            "Answer Type": "Phrases",
            "Answer Weightage": base["Marks"],
            "Answer Content": "Step 1: state property (1 mark) // Step 2: justify (1 mark) // Step 3: conclude (1 mark)",
            "Answer Explanation": "Awarded per step; partial credit allowed.",
            "Display Answer": "See solution key.",
        })
    return base


# --------------------------------------------------------------------------- #
# Live-mode wrappers (best-effort delegation to vendored scripts)
# --------------------------------------------------------------------------- #

def _live_extract_pdfs(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    from aegis_pipeline import extract_pdfs  # noqa: F401  (vendored module)
    raise NotImplementedError(
        "Live PDF extraction expects a chapter URL list; configure inputs and "
        "wire to extract_pdfs.main(). Falling back to dry mode is recommended."
    )


def _live_extract_mmds(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    from aegis_pipeline import extract_mmds  # noqa: F401
    raise NotImplementedError(
        "Live Mathpix extraction needs MATHPIX_APP_ID + MATHPIX_APP_KEY and a "
        "PDF directory; call extract_mmds.process_directory(...) once paths are wired."
    )


def _live_mmd_to_concepts(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    from aegis_pipeline import mmd_to_concepts_excel  # noqa: F401
    raise NotImplementedError(
        "Live concept extraction needs OPENAI_API_KEY and an .mmd directory; "
        "invoke mmd_to_concepts_excel.process_mmd_file(...) per file."
    )


def _live_excel_to_prelearning(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    from aegis_pipeline import excel_to_concepts_prelearning  # noqa: F401
    raise NotImplementedError("Live mode requires OPENAI_API_KEY and a Concepts.xlsx input.")


def _live_concept_mapping_to_prelearning(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    from aegis_pipeline import concept_mapping_to_prelearning  # noqa: F401
    raise NotImplementedError("Live mode requires OPENAI_API_KEY and a concept mapping workbook.")


def _live_bulk_upload(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    from aegis_pipeline import bulk_upload_ultimate  # noqa: F401
    raise NotImplementedError("Live mode requires MATHPIX + OPENAI keys and Q+A files.")


def _live_assessment_tagging(out: Path, inp: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError(
        "Assessment tagging is a Google Apps Script project; live mode would call "
        "the deployed Apps Script web endpoint. Use dry mode in this MVP."
    )


# --------------------------------------------------------------------------- #
# Stage registry
# --------------------------------------------------------------------------- #

STAGES: dict[str, Stage] = {
    "extract_pdfs": Stage(
        key="extract_pdfs",
        title="1. Extract PDFs",
        order=1,
        description="Discover chapter PDFs from index URLs and download to local storage.",
        inputs=["chapter_codes", "index_url"],
        outputs=["manifest.json", "*.pdf"],
        dependencies=[],
        requires_keys=[],
        dry=_dry_extract_pdfs,
        live=_live_extract_pdfs,
    ),
    "extract_mmds": Stage(
        key="extract_mmds",
        title="2. PDF → MMD (Mathpix OCR)",
        order=2,
        description="Convert PDFs to Mathpix Markdown (.mmd) preserving math.",
        inputs=["pdf_dir"],
        outputs=["*.mmd"],
        dependencies=["extract_pdfs"],
        requires_keys=["MATHPIX_APP_ID", "MATHPIX_APP_KEY"],
        dry=_dry_extract_mmds,
        live=_live_extract_mmds,
    ),
    "mmd_to_concepts": Stage(
        key="mmd_to_concepts",
        title="3. MMD → Concepts Excel",
        order=3,
        description="Use GPT to extract 40-60 structured concepts per chapter.",
        inputs=["mmd_dir"],
        outputs=["concepts.xlsx"],
        dependencies=["extract_mmds"],
        requires_keys=["OPENAI_API_KEY"],
        dry=_dry_mmd_to_concepts,
        live=_live_mmd_to_concepts,
    ),
    "excel_to_prelearning": Stage(
        key="excel_to_prelearning",
        title="4a. Excel → Pre-Learning Concepts",
        order=4,
        description="Enhance a user-supplied concept list with parent concepts and Type/Misconception sections.",
        inputs=["concepts_xlsx"],
        outputs=["pre_learning_concepts.xlsx"],
        dependencies=["mmd_to_concepts"],
        requires_keys=["OPENAI_API_KEY"],
        dry=_dry_excel_to_prelearning,
        live=_live_excel_to_prelearning,
    ),
    "concept_mapping_to_prelearning": Stage(
        key="concept_mapping_to_prelearning",
        title="4b. Concept Mapping → Pre-Learning",
        order=5,
        description="Run the full concept-mapping pipeline with syllabus-boundary filter.",
        inputs=["concept_mapping_xlsx"],
        outputs=["pre_learning_concepts.xlsx"],
        dependencies=["mmd_to_concepts"],
        requires_keys=["OPENAI_API_KEY"],
        dry=_dry_concept_mapping_to_prelearning,
        live=_live_concept_mapping_to_prelearning,
    ),
    "bulk_upload": Stage(
        key="bulk_upload",
        title="5. Bulk Question Upload",
        order=6,
        description="Parse Q&A PDFs, classify, extract marks, build 3-sheet bulk upload workbook.",
        inputs=["questions_path", "solutions_path"],
        outputs=["final_output.xlsx"],
        dependencies=[],
        requires_keys=["MATHPIX_APP_ID", "MATHPIX_APP_KEY", "OPENAI_API_KEY"],
        dry=_dry_bulk_upload,
        live=_live_bulk_upload,
    ),
    "assessment_tagging": Stage(
        key="assessment_tagging",
        title="6. Assessment Tagging",
        order=7,
        description="Route questions to topics/concepts and update the assessment sheet.",
        inputs=["chapter_code", "mode"],
        outputs=["tagging_decisions.json"],
        dependencies=["bulk_upload", "concept_mapping_to_prelearning"],
        requires_keys=["OPENAI_API_KEY"],
        dry=_dry_assessment_tagging,
        live=_live_assessment_tagging,
    ),
}


def list_stages() -> list[schemas.StageDescriptor]:
    return [
        schemas.StageDescriptor(
            key=s.key, title=s.title, order=s.order, description=s.description,
            inputs=s.inputs, outputs=s.outputs, dependencies=s.dependencies,
            requires_keys=s.requires_keys,
            available=_has_keys(s.requires_keys),
        )
        for s in sorted(STAGES.values(), key=lambda s: s.order)
    ]


def run_stage(db: Session, stage_key: str, request: schemas.StageRunRequest) -> models.PipelineRun:
    stage = STAGES.get(stage_key)
    if stage is None:
        raise KeyError(f"Unknown stage: {stage_key}")

    run_id = uuid.uuid4().hex[:12]
    out_dir = ARTIFACT_ROOT / stage.key / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    run = models.PipelineRun(
        stage=stage.key, mode=request.mode, status="running",
        phase="executing", inputs=request.inputs or {}, artifact_path=str(out_dir),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    started = time.time()
    try:
        if request.mode == "live":
            if not _has_keys(stage.requires_keys):
                raise RuntimeError(
                    f"Live mode requires env vars: {', '.join(stage.requires_keys)}"
                )
            if stage.live is None:
                raise RuntimeError("Live mode is not implemented for this stage.")
            outputs = stage.live(out_dir, request.inputs or {})
        else:
            outputs = stage.dry(out_dir, request.inputs or {})
        run.outputs = outputs
        run.status = "succeeded"
        run.phase = "done"
        run.progress = 1.0
        run.detail = f"Completed in {time.time() - started:.2f}s"
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        run.detail = str(exc)
    finally:
        run.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
    return run


def list_runs(db: Session, stage: str | None = None, limit: int = 50) -> list[models.PipelineRun]:
    q = db.query(models.PipelineRun)
    if stage:
        q = q.filter(models.PipelineRun.stage == stage)
    return q.order_by(models.PipelineRun.id.desc()).limit(limit).all()


def get_artifact_path(run: models.PipelineRun, name: str) -> Path | None:
    if not run.artifact_path:
        return None
    candidate = Path(run.artifact_path) / name
    return candidate if candidate.exists() else None


def cleanup_artifacts() -> None:
    """Remove all artifact directories; safe to call between tests."""
    if ARTIFACT_ROOT.exists():
        shutil.rmtree(ARTIFACT_ROOT)
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
