"""Generate the seed Bulk Import database workbook.

The integrated tool treats a Bulk Import workbook as its primary database, so
the fixture is itself a canonical workbook (``data/bulk_import_database.xlsx``).
We seed ORM objects into an in-memory SQLite DB and then write them out
through the real ``bulk_import.writer``, which guarantees the fixture always
matches the canonical column layout.

The blueprint below covers both boards (CBSE / ICSE), both grades (09 / 10),
and six subjects so the UI has enough breadth to exercise filters, scopes and
question authoring across the three sheet kinds. Rich-text fields follow the
bracket conventions captured in ``app.services.katex_rules``:
  - equations:  [katex] ... [/katex]
  - images:     [img src="https://..." alt="..."]
  - links:      [Display Text](https://...)
Keyword cells in the descriptive sheet stay raw (no [katex] wrappers).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config, models
from app.bulk_import import writer
from app.db import Base
from app.services import directory
from app.services import katex_rules as kr

DATA_DIR = config.DATA_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEST = config.BULK_IMPORT_DB

MATH_LIKE = {"Mathematics", "Physics", "Chemistry"}

# --------------------------------------------------------------------------- #
# Content blueprint
# --------------------------------------------------------------------------- #
# Each concept entry is (concept_title, short_summary, katex_formula_or_None).
# katex strings are LaTeX bodies — they get wrapped in [katex]..[/katex] when
# emitted. ``None`` means the concept has no canonical formula.

BLUEPRINT = [
    # ---- CBSE 10 Mathematics ------------------------------------------------
    ("CBSE", "10", "Mathematics", "Circles", [
        ("Tangents to a Circle", [
            ("Chord versus Tangent",
             "A chord joins two points of a circle; a tangent meets the circle at exactly one point.",
             r"\text{chord length} = 2\sqrt{r^2 - d^2}"),
            ("Tangent Perpendicular to Radius",
             "The radius drawn to the point of contact is perpendicular to the tangent at that point.",
             r"\overline{OP} \perp \overline{PT}"),
        ]),
        ("Number of Tangents from a Point", [
            ("Tangents from an External Point",
             "From any external point, exactly two tangents can be drawn to a circle and they are equal in length.",
             r"PT_1 = PT_2"),
            ("Tangent Length Formula",
             "The length of a tangent from an external point equals the square root of (distance squared minus radius squared).",
             r"PT = \sqrt{OP^2 - r^2}"),
        ]),
    ]),
    ("CBSE", "10", "Mathematics", "Real Numbers", [
        ("Euclid's Division Lemma", [
            ("Statement of the Lemma",
             "For any positive integers a and b there exist unique integers q and r such that a = bq + r with 0 <= r < b.",
             r"a = bq + r,\ 0 \le r < b"),
            ("Finding HCF by Division",
             "Repeatedly applying the lemma to (b, r) eventually yields r = 0; the last non-zero divisor is the HCF.",
             r"\gcd(a,b) = \gcd(b,r)"),
        ]),
        ("Fundamental Theorem of Arithmetic", [
            ("Unique Prime Factorisation",
             "Every composite number can be expressed as a product of primes, and this factorisation is unique up to order.",
             r"n = p_1^{a_1} p_2^{a_2} \cdots p_k^{a_k}"),
        ]),
    ]),
    ("CBSE", "10", "Mathematics", "Quadratic Equations", [
        ("Solutions of a Quadratic", [
            ("Quadratic Formula",
             "The roots of ax^2 + bx + c = 0 are given by the discriminant formula.",
             r"x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}"),
            ("Nature of Roots",
             "The sign of the discriminant decides whether roots are real-distinct, real-equal or complex.",
             r"\Delta = b^2 - 4ac"),
        ]),
    ]),
    # ---- CBSE 10 Physics ----------------------------------------------------
    ("CBSE", "10", "Physics", "Light - Reflection and Refraction", [
        ("Spherical Mirrors", [
            ("Mirror Formula",
             "The mirror formula relates object distance u, image distance v and focal length f.",
             r"\frac{1}{v} + \frac{1}{u} = \frac{1}{f}"),
            ("Magnification by a Mirror",
             "Magnification is the ratio of image height to object height, equal to -v/u for mirrors.",
             r"m = \frac{h'}{h} = -\frac{v}{u}"),
        ]),
        ("Refraction through a Lens", [
            ("Lens Formula",
             "The lens formula for a thin lens uses the sign convention with object distance taken as negative.",
             r"\frac{1}{v} - \frac{1}{u} = \frac{1}{f}"),
        ]),
    ]),
    ("CBSE", "10", "Physics", "Electricity", [
        ("Ohm's Law", [
            ("Statement and Formula",
             "Current through a conductor is directly proportional to the potential difference across it at constant temperature.",
             r"V = IR"),
            ("Resistance and Resistivity",
             "Resistance of a wire depends on length, cross-sectional area and the material's resistivity.",
             r"R = \rho \frac{L}{A}"),
        ]),
        ("Electric Power", [
            ("Power Dissipated in a Resistor",
             "Power dissipated equals VI; equivalently I^2 R or V^2 / R.",
             r"P = VI = I^2 R = \frac{V^2}{R}"),
        ]),
    ]),
    # ---- CBSE 09 Biology ----------------------------------------------------
    ("CBSE", "09", "Biology", "The Fundamental Unit of Life", [
        ("Cell Organelles", [
            ("Mitochondria - the Powerhouse",
             "Mitochondria synthesise ATP via oxidative phosphorylation and have their own DNA.",
             None),
            ("Endoplasmic Reticulum",
             "Rough ER is studded with ribosomes for protein synthesis; smooth ER handles lipid synthesis.",
             None),
        ]),
        ("Plant vs Animal Cell", [
            ("Cell Wall and Chloroplast",
             "Plant cells have a rigid cell wall and chloroplasts; animal cells have neither.",
             None),
        ]),
    ]),
    # ---- ICSE 10 English Grammar -------------------------------------------
    ("ICSE", "10", "English Grammar", "Tenses", [
        ("Past Tense", [
            ("Past Continuous",
             "Describes actions in progress at a point in the past; formed with was/were + verb-ing.",
             None),
            ("Past Perfect",
             "Used for an action completed before another past action; formed with had + past participle.",
             None),
        ]),
        ("Present Tense", [
            ("Present Perfect",
             "Indicates an action that started in the past and continues, or has just been completed; uses has/have + past participle.",
             None),
        ]),
    ]),
    ("ICSE", "10", "English Grammar", "Voice and Narration", [
        ("Active and Passive Voice", [
            ("Transforming Active to Passive",
             "The object of the active sentence becomes the subject of the passive; the verb takes a form of be + past participle.",
             None),
        ]),
    ]),
    # ---- ICSE 10 English Literature ----------------------------------------
    ("ICSE", "10", "English Literature", "Treasure Trove Poems", [
        ("The Heart of the Tree", [
            ("Theme of Conservation",
             "Bushnell's poem celebrates the planter as a benefactor of future generations, linking trees to civic virtue.",
             None),
        ]),
        ("The Cold Within", [
            ("Symbolism of the Six Logs",
             "Each character withholds a log out of prejudice; together they freeze - the cold within is hatred.",
             None),
        ]),
    ]),
    # ---- ICSE 09 Chemistry --------------------------------------------------
    ("ICSE", "09", "Chemistry", "Atomic Structure", [
        ("Bohr's Model", [
            ("Quantised Orbits",
             "Electrons revolve in stationary orbits whose angular momentum is an integral multiple of h/2 pi.",
             r"m v r = \frac{n h}{2 \pi}"),
            ("Energy of an Electron",
             "The energy of an electron in the n-th orbit of hydrogen is given by the Rydberg expression.",
             r"E_n = -\frac{13.6}{n^2}\ \text{eV}"),
        ]),
        ("Electron Configuration", [
            ("2n^2 Rule",
             "The maximum number of electrons in the n-th shell equals 2 n squared.",
             r"N_{\max} = 2n^2"),
        ]),
    ]),
    # ---- ICSE 10 Chemistry --------------------------------------------------
    ("ICSE", "10", "Chemistry", "Acids Bases and Salts", [
        ("pH Scale", [
            ("Definition of pH",
             "pH is the negative logarithm to base 10 of the hydrogen ion concentration in mol/L.",
             r"\mathrm{pH} = -\log_{10}[\mathrm{H}^+]"),
        ]),
    ]),
]


def _slug(text: str, n: int = 14) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", text.title())[:n] or "X"


def _label(chapter_code: str, topic_no: int, concept_slug: str, g_code: str, q_no: int) -> str:
    return f"{chapter_code}_PL_T{topic_no:02d}_{concept_slug}_{g_code} Q{q_no:02d}"


def _ref_link(concept_title: str, subject: str) -> str:
    q = quote_plus(f"{concept_title} {subject}".strip())
    return kr.link(concept_title, f"https://en.wikipedia.org/wiki/Special:Search?search={q}")


def _katex_or_text(formula: str | None, fallback: str) -> str:
    return kr.katex(formula) if formula else fallback


# --------------------------------------------------------------------------- #
# Per-kind question templates
# --------------------------------------------------------------------------- #

OBJECTIVE_CATEGORIES = [
    "Multiple Choice Question", "Assertion & Reasons",
    "True/False", "Fill in the Blanks",
]
SUBJECTIVE_CATEGORIES = [
    "Short Answer", "Very Short Answer", "Fill in the Blanks",
    "Sentence Transformation",
]
DESCRIPTIVE_CATEGORIES = [
    "Long Answer", "Case Based Questions", "Passage Based Questions",
]
COG = ["Remembering", "Understanding", "Applying", "Analysing", "Evaluating", "Creating"]
DIFF_BY_GROUP = {"Basic": "Less", "Intermediate": "Moderate", "Advanced": "High"}
GROUP_CODE = {"Basic": "BG", "Intermediate": "IG", "Advanced": "AG"}


def _objective_question(concept_title: str, summary: str, formula: str | None,
                        subject: str, idx: int) -> dict:
    category = OBJECTIVE_CATEGORIES[idx % len(OBJECTIVE_CATEGORIES)]
    cog = COG[idx % len(COG)]
    is_math = subject in MATH_LIKE and formula
    if category == "Multiple Choice Question":
        if is_math:
            q = f"Which expression correctly states the result for {concept_title}? {kr.katex(formula)}"
        else:
            q = f"Which of the following best describes {concept_title}?"
        answers = [
            {"answer_type": "Words", "answer_content": f"{summary}",
             "correct_answer": "Yes", "answer_weightage": "1"},
            {"answer_type": "Words", "answer_content": f"Common misconception about {concept_title.lower()}.",
             "correct_answer": "No", "answer_weightage": "0"},
            {"answer_type": "Words", "answer_content": f"Unrelated property mistakenly linked to {concept_title.lower()}.",
             "correct_answer": "No", "answer_weightage": "0"},
            {"answer_type": "Words", "answer_content": "None of the above.",
             "correct_answer": "No", "answer_weightage": "0"},
        ]
    elif category == "True/False":
        q = f"State true or false: {summary}"
        answers = [
            {"answer_type": "Words", "answer_content": "True",
             "correct_answer": "Yes", "answer_weightage": "1"},
            {"answer_type": "Words", "answer_content": "False",
             "correct_answer": "No", "answer_weightage": "0"},
        ]
    elif category == "Fill in the Blanks":
        q = f"Fill in the blank: ____ is the key idea behind '{concept_title}'."
        answers = [
            {"answer_type": "Words", "answer_content": concept_title,
             "correct_answer": "Yes", "answer_weightage": "1"},
        ]
    else:  # Assertion & Reasons
        q = (f"Assertion (A): {summary} "
             f"Reason (R): It follows from the standard treatment of {concept_title.lower()}.")
        answers = [
            {"answer_type": "Words",
             "answer_content": "Both A and R are true and R correctly explains A.",
             "correct_answer": "Yes", "answer_weightage": "1"},
            {"answer_type": "Words",
             "answer_content": "Both A and R are true but R does not explain A.",
             "correct_answer": "No", "answer_weightage": "0"},
            {"answer_type": "Words", "answer_content": "A is true but R is false.",
             "correct_answer": "No", "answer_weightage": "0"},
            {"answer_type": "Words", "answer_content": "A is false but R is true.",
             "correct_answer": "No", "answer_weightage": "0"},
        ]
    return {
        "category": category, "cognitive_skills": cog,
        "question": q, "marks": 1,
        "answers": answers,
        "answer_explanation": (
            f"{summary} See {_ref_link(concept_title, subject)} for more."
            + (f" Key relation: {kr.katex(formula)}." if is_math else "")
        ),
    }


def _subjective_question(concept_title: str, summary: str, formula: str | None,
                         subject: str, idx: int) -> dict:
    category = SUBJECTIVE_CATEGORIES[idx % len(SUBJECTIVE_CATEGORIES)]
    cog = COG[(idx + 1) % len(COG)]
    is_math = subject in MATH_LIKE and formula
    if category == "Very Short Answer":
        q = f"In one sentence, define '{concept_title}'."
        ans = summary
        marks = 2
    elif category == "Fill in the Blanks":
        q = f"Complete: The key principle of '{concept_title}' is that ____."
        ans = summary
        marks = 2
    elif category == "Sentence Transformation":
        q = f"Rewrite the following idea in your own words: {summary}"
        ans = f"Restated: {summary}"
        marks = 3
    else:  # Short Answer
        q = f"Explain '{concept_title}' with a short example."
        ans = f"{summary} " + (kr.katex(formula) if is_math else "Provide a worked example here.")
        marks = 3
    return {
        "category": category, "cognitive_skills": cog,
        "question": q, "marks": marks,
        "answers": [
            {"answer_type": "Words", "answer": ans,
             "answer_display": "Yes", "weightage": str(marks), "placeholder": "answer"},
        ],
        "answer_explanation": (
            f"Expected response highlights {concept_title.lower()}. "
            f"Reference: {_ref_link(concept_title, subject)}."
        ),
    }


def _descriptive_question(concept_title: str, summary: str, formula: str | None,
                          subject: str, idx: int) -> dict:
    category = DESCRIPTIVE_CATEGORIES[idx % len(DESCRIPTIVE_CATEGORIES)]
    cog = COG[(idx + 2) % len(COG)]
    is_math = subject in MATH_LIKE and formula
    q = (f"Discuss '{concept_title}' in detail with definitions, derivations and an example. "
         + (kr.katex(formula) if is_math else ""))
    body = (f"{summary} A worked example should be included. "
            + (f"Key relation: {kr.katex(formula)}. " if is_math else "")
            + f"Reference: {_ref_link(concept_title, subject)}.")
    marks = 5
    answers = [
        {"answer_type": "Words", "answer_weightage": str(marks), "answer_content": body},
    ]
    # keyword cells are raw, no [katex] wrappers
    sub_questions = [
        {"text": f"i. Define '{concept_title}' in your own words.", "marks": "2",
         "keywords": [{"answer_type": "Words", "weightage": "2",
                       "keyword": concept_title}]},
        {"text": f"ii. Provide a worked example illustrating '{concept_title}'.",
         "marks": "3",
         "keywords": [{"answer_type": "Words", "weightage": "3",
                       "keyword": formula if (is_math and formula) else "worked example"}]},
    ]
    return {
        "category": category, "cognitive_skills": cog,
        "question": q, "marks": marks,
        "answers": answers,
        "sub_questions": sub_questions,
        "answer_explanation": (
            f"Award marks for defining {concept_title.lower()}, applying it correctly, "
            f"and presenting a clean worked example. Reference: {_ref_link(concept_title, subject)}."
        ),
    }


# --------------------------------------------------------------------------- #
# Seeder
# --------------------------------------------------------------------------- #

def _seed(db) -> None:
    for board, grade, subject, ch_title, topics in BLUEPRINT:
        chapter_code = directory.make_chapter_code(board, grade, subject, ch_title)
        chapter = models.Chapter(
            chapter_code=chapter_code, board=board, grade=grade, subject=subject,
            unit=f"{subject} Unit",
            chapter_title=ch_title,
            chapter_display_name=directory.chapter_titled_cell(
                ch_title, board, grade, subject, book="NCERT"),
            chapter_duration="3",
            chapter_description=f"{board} Grade {grade} {subject}: {ch_title}.",
        )
        db.add(chapter)
        db.flush()

        for t_idx, (topic_title, concepts) in enumerate(topics, start=1):
            topic = models.Topic(
                chapter_id=chapter.id,
                topic_title=f"Topic {t_idx:02d}: {topic_title} ({chapter_code}_PL)",
                topic_display_name=topic_title,
                pre_post_learning="Post",
                topic_description=f"{topic_title}: {board} Grade {grade} {subject} topic.",
            )
            db.add(topic)
            db.flush()

            for c_idx, (concept_title, summary, formula) in enumerate(concepts, start=1):
                slug = _slug(concept_title)
                concept = models.Concept(
                    topic_id=topic.id,
                    concept_title=concept_title,
                    concept_display_name=(
                        f"{concept_title} ({chapter_code}_PL_{topic_title.replace(' ', '_')})"
                    ),
                    concept_details=(
                        f"Description: {summary} "
                        f"// Types: Type 01 Application Case 01 standard worked example "
                        f"// Misconception: confusing this concept with neighbouring ideas."
                    ),
                    keywords=", ".join(concept_title.lower().split()),
                )
                db.add(concept)
                db.flush()

                # Build all three group types, each with 2 questions of its sheet kind.
                kind_for_group = {
                    "Basic": ("objective", _objective_question),
                    "Intermediate": ("subjective", _subjective_question),
                    "Advanced": ("descriptive", _descriptive_question),
                }
                for g_type, (kind, builder) in kind_for_group.items():
                    group = models.Group(
                        concept_id=concept.id, group_type=g_type,
                        group_name=f"({chapter_code}_PL_T{t_idx:02d}_{slug}) {GROUP_CODE[g_type]}{c_idx:02d}",
                        group_display_name=f"{concept_title} - {g_type}",
                        group_description=f"{g_type} group for {concept_title}.",
                        group_status="Active",
                    )
                    db.add(group)
                    db.flush()
                    for n in range(1, 3):
                        rec = builder(concept_title, summary, formula, subject, n - 1)
                        q = models.Question(
                            group_id=group.id, sheet_kind=kind,
                            question_label=_label(chapter_code, t_idx, slug,
                                                  GROUP_CODE[g_type], (c_idx - 1) * 2 + n),
                            question_category=rec["category"],
                            cognitive_skills=rec["cognitive_skills"],
                            question_source="Aegis Seed",
                            level_of_difficulty=DIFF_BY_GROUP[g_type],
                            question=rec["question"],
                            marks=rec["marks"],
                            answer_explanation=rec["answer_explanation"],
                            answers=rec["answers"],
                            sub_questions=rec.get("sub_questions", []),
                            display_answer="Yes" if kind == "descriptive" else "",
                            origin="seed",
                        )
                        db.add(q)
    db.commit()


def main() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        _seed(db)
        writer.write_workbook(db, dest=DEST)
        counts = {
            "chapters": db.query(models.Chapter).count(),
            "topics": db.query(models.Topic).count(),
            "concepts": db.query(models.Concept).count(),
            "groups": db.query(models.Group).count(),
            "questions": db.query(models.Question).count(),
        }
        print(f"wrote {DEST}")
        print(" ", counts)
    finally:
        db.close()


if __name__ == "__main__":
    main()
