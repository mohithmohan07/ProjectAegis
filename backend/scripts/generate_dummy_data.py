"""Generate canonical Aegis dummy fixtures.

Writes:
- data/concepts.xlsx          (sheet "Concepts") matching mmd_to_concepts_excel.py
- data/pre_learning.xlsx      (sheet "Concepts") matching excel_to_concepts_prelearning.py
- data/bulk_upload.xlsx       (sheets Objective | Subjective | Descriptive)
- data/manifest.json          (fake PDF manifest mirroring extract_pdfs)

The same fixtures are committed as CSV equivalents so the repo is browseable.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONCEPTS = [
    {
        "Board": "ICSE", "Book": "SE", "Grade": "09", "Subject": "Mathematics",
        "Chapter No": "01", "Chapter Code": "09ICMA_CH01",
        "Chapter Title": "Number Systems",
        "Topic": "Topic 01: Real Numbers",
        "Parent Concept": "",
        "Concept": "Rational Numbers",
        "Concept Description": "A rational number can be expressed as p/q where q != 0.",
        "Concept ID": "09ICMA_CH01-C001",
        "MMD Path": "data/mmds/09ICMA_CH01.mmd",
        "PDF Path": "data/pdfs/09ICMA_CH01.pdf",
    },
    {
        "Board": "ICSE", "Book": "SE", "Grade": "09", "Subject": "Mathematics",
        "Chapter No": "01", "Chapter Code": "09ICMA_CH01",
        "Chapter Title": "Number Systems",
        "Topic": "Topic 01: Real Numbers",
        "Parent Concept": "Rational Numbers",
        "Concept": "Irrational Numbers",
        "Concept Description": "Real numbers that cannot be expressed as p/q.",
        "Concept ID": "09ICMA_CH01-C002",
        "MMD Path": "data/mmds/09ICMA_CH01.mmd",
        "PDF Path": "data/pdfs/09ICMA_CH01.pdf",
    },
    {
        "Board": "ICSE", "Book": "SE", "Grade": "09", "Subject": "Mathematics",
        "Chapter No": "02", "Chapter Code": "09ICMA_CH02",
        "Chapter Title": "Polynomials",
        "Topic": "Topic 01: Polynomial Basics",
        "Parent Concept": "",
        "Concept": "Degree of a Polynomial",
        "Concept Description": "Highest power of the variable in a polynomial.",
        "Concept ID": "09ICMA_CH02-C001",
        "MMD Path": "data/mmds/09ICMA_CH02.mmd",
        "PDF Path": "data/pdfs/09ICMA_CH02.pdf",
    },
    {
        "Board": "ICSE", "Book": "SE", "Grade": "09", "Subject": "Physics",
        "Chapter No": "03", "Chapter Code": "09ICPH_CH03",
        "Chapter Title": "Laws of Motion",
        "Topic": "Topic 02: Newton's Laws",
        "Parent Concept": "",
        "Concept": "Newton's Third Law",
        "Concept Description": "Every action has an equal and opposite reaction.",
        "Concept ID": "09ICPH_CH03-C005",
        "MMD Path": "data/mmds/09ICPH_CH03.mmd",
        "PDF Path": "data/pdfs/09ICPH_CH03.pdf",
    },
    {
        "Board": "ICSE", "Book": "SE", "Grade": "09", "Subject": "Physics",
        "Chapter No": "03", "Chapter Code": "09ICPH_CH03",
        "Chapter Title": "Laws of Motion",
        "Topic": "Topic 03: Friction",
        "Parent Concept": "",
        "Concept": "Coefficient of Friction",
        "Concept Description": "Ratio of friction force to normal force.",
        "Concept ID": "09ICPH_CH03-C010",
        "MMD Path": "data/mmds/09ICPH_CH03.mmd",
        "PDF Path": "data/pdfs/09ICPH_CH03.pdf",
    },
]


def _pre_learning_rows() -> list[dict]:
    enriched = []
    for c in CONCEPTS:
        row = dict(c)
        row["Concept Description"] = (
            f"Description: {c['Concept Description']} "
            "// Types: Type 01: Standard Case 01: introductory example "
            "// Misconception: students often confuse this with related concepts."
        )
        enriched.append(row)
    return enriched


def _objective_rows() -> list[dict]:
    return [
        {
            "Question Label": "09ICMA_CH01_PL_Q01",
            "Question Category": "Multiple Choice Question",
            "Cognitive Skills": "Remembering",
            "Question Source": "ICSE Past Paper 2024",
            "Question Appears in": "Chapter 1 - Number Systems",
            "Level of Difficulty": "Less",
            "Question": "Which of the following is a rational number?",
            "Marks": 1,
            "Answer Type1": "Phrases", "Answer Content1": "1/2", "Correct Answer1": "TRUE", "Answer Weightage1": 1,
            "Answer Type2": "Phrases", "Answer Content2": "sqrt(2)", "Correct Answer2": "FALSE", "Answer Weightage2": 0,
            "Answer Type3": "Phrases", "Answer Content3": "pi", "Correct Answer3": "FALSE", "Answer Weightage3": 0,
            "Answer Type4": "Phrases", "Answer Content4": "e", "Correct Answer4": "FALSE", "Answer Weightage4": 0,
            "Answer Explanation": "1/2 fits the form p/q with integer p,q and q != 0.",
        },
        {
            "Question Label": "09ICPH_CH03_PL_Q01",
            "Question Category": "True/False",
            "Cognitive Skills": "Understanding",
            "Question Source": "ICSE Sample 2023",
            "Question Appears in": "Chapter 3 - Laws of Motion",
            "Level of Difficulty": "Less",
            "Question": "Newton's third law states that for every action there is an equal and opposite reaction.",
            "Marks": 1,
            "Answer Type1": "Phrases", "Answer Content1": "True", "Correct Answer1": "TRUE", "Answer Weightage1": 1,
            "Answer Type2": "Phrases", "Answer Content2": "False", "Correct Answer2": "FALSE", "Answer Weightage2": 0,
            "Answer Explanation": "Direct statement of the law.",
        },
    ]


def _subjective_rows() -> list[dict]:
    return [
        {
            "Question Label": "09ICMA_CH01_PL_Q05",
            "Question Category": "Short Answer (3 marks)",
            "Cognitive Skills": "Applying",
            "Question Source": "ICSE Past Paper 2024",
            "Question Appears in": "Chapter 1 - Number Systems",
            "Level of Difficulty": "Moderate",
            "Question": "Express 0.8333... as a rational number in the form p/q.",
            "Marks": 3,
            "Answer Type": "Phrases",
            "Answer Weightage": 3,
            "Answer Content": "Step 1: let x = 0.8333... (1 mark) // Step 2: 10x - x = 7.5 (1 mark) // Step 3: x = 5/6 (1 mark)",
            "Answer Explanation": "Use repeating decimal trick to convert.",
            "Display Answer": "5/6",
        },
    ]


def _descriptive_rows() -> list[dict]:
    return [
        {
            "Question Label": "09ICPH_CH03_PL_Q08",
            "Question Category": "Long Answer (5 marks)",
            "Cognitive Skills": "Analysing",
            "Question Source": "ICSE Past Paper 2023",
            "Question Appears in": "Chapter 3 - Laws of Motion",
            "Level of Difficulty": "High",
            "Question": "Derive the equation for friction force on a block on an inclined plane and explain assumptions.",
            "Marks": 5,
            "Answer Type": "Phrases",
            "Answer Weightage": 5,
            "Answer Content": (
                "Step 1: free body diagram (1 mark) // Step 2: resolve forces along/perpendicular to incline (1 mark) "
                "// Step 3: write friction = mu * N (1 mark) // Step 4: substitute N = mg cos(theta) (1 mark) "
                "// Step 5: state assumption: rigid body, no air drag (1 mark)"
            ),
            "Answer Explanation": "Standard inclined-plane derivation; mark per labelled step.",
            "Display Answer": "F = mu * mg * cos(theta)",
        },
    ]


def main() -> None:
    pd.DataFrame(CONCEPTS).to_csv(DATA_DIR / "concepts.csv", index=False)
    with pd.ExcelWriter(DATA_DIR / "concepts.xlsx", engine="openpyxl") as w:
        pd.DataFrame(CONCEPTS).to_excel(w, index=False, sheet_name="Concepts")

    pd.DataFrame(_pre_learning_rows()).to_csv(DATA_DIR / "pre_learning.csv", index=False)
    with pd.ExcelWriter(DATA_DIR / "pre_learning.xlsx", engine="openpyxl") as w:
        pd.DataFrame(_pre_learning_rows()).to_excel(w, index=False, sheet_name="Concepts")

    with pd.ExcelWriter(DATA_DIR / "bulk_upload.xlsx", engine="openpyxl") as w:
        pd.DataFrame(_objective_rows()).to_excel(w, index=False, sheet_name="Objective")
        pd.DataFrame(_subjective_rows()).to_excel(w, index=False, sheet_name="Subjective")
        pd.DataFrame(_descriptive_rows()).to_excel(w, index=False, sheet_name="Descriptive")
    pd.DataFrame(_objective_rows()).to_csv(DATA_DIR / "bulk_upload_objective.csv", index=False)
    pd.DataFrame(_subjective_rows()).to_csv(DATA_DIR / "bulk_upload_subjective.csv", index=False)
    pd.DataFrame(_descriptive_rows()).to_csv(DATA_DIR / "bulk_upload_descriptive.csv", index=False)

    manifest = [
        {"chapter_code": c["Chapter Code"], "drive_ids": [f"drive-stub-{c['Chapter Code']}"],
         "local_pdf_path": c["PDF Path"], "status": "PDF_DOWNLOADED"}
        for c in CONCEPTS
    ]
    (DATA_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))

    for c in CONCEPTS:
        mmd_path = DATA_DIR.parent / c["MMD Path"]
        mmd_path.parent.mkdir(parents=True, exist_ok=True)
        if not mmd_path.exists():
            mmd_path.write_text(
                f"# {c['Chapter Title']}\n\n## {c['Topic']}\n\n"
                f"### {c['Concept']}\n{c['Concept Description']}\n"
            )

    print(f"wrote canonical fixtures into {DATA_DIR}")


if __name__ == "__main__":
    main()
