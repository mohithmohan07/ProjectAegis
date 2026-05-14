"""Generate the dummy Bulk Import database workbook.

The integrated tool treats a Bulk Import workbook as its database, so the
fixture is itself a canonical workbook (``data/bulk_import_database.xlsx``).
It is built by seeding ORM objects into a throwaway SQLite DB and writing them
out through the real ``bulk_import.writer`` — guaranteeing the fixture always
matches the canonical layout.

Replace this file with the real Clarius Bulk Import workbook later; nothing
else needs to change.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models
from app.bulk_import import writer
from app.services import directory

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEST = DATA_DIR / "bulk_import_database.xlsx"


def _label(chapter_code: str, topic_no: int, concept_slug: str, g_type: str, n: int) -> str:
    return f"{chapter_code}_PL_T{topic_no:02d}_{concept_slug}_{g_type[:1]}G Q{n:02d}"


def _seed(db) -> None:
    blueprint = [
        # board, grade, subject, chapter_title, [topics...]
        ("CBSE", "10", "Mathematics", "Circles", [
            ("Secant and Tangent - Definitions and Positions", [
                ("Conceptual Meaning of Chord versus Tangent",
                 "A chord joins two points of a circle; a tangent meets it at exactly one point."),
                ("Position of a Line Relative to a Circle",
                 "A line can miss, touch, or cut a circle - giving 0, 1, or 2 intersection points."),
            ]),
            ("Perpendicularity and Construction of Tangent", [
                ("Tangent is Perpendicular to Radius at Point of Contact",
                 "The radius drawn to the point of contact is perpendicular to the tangent."),
                ("Constructing a Tangent from an External Point",
                 "Two tangents can be drawn to a circle from any external point; they are equal in length."),
            ]),
        ]),
        ("ICSE", "09", "Physics", "Laws of Motion", [
            ("Newton's Laws", [
                ("Newton's First Law and Inertia",
                 "A body continues in its state of rest or uniform motion unless acted on by a force."),
                ("Newton's Third Law",
                 "Every action has an equal and opposite reaction."),
            ]),
            ("Friction", [
                ("Static and Kinetic Friction",
                 "Static friction prevents motion up to a limit; kinetic friction opposes ongoing sliding."),
            ]),
        ]),
        ("ICSE", "10", "English Grammar", "Tenses", [
            ("Past Tense", [
                ("Past Continuous Tense",
                 "Describes actions in progress at a point in the past, formed with was/were + -ing."),
            ]),
        ]),
    ]

    for board, grade, subject, ch_title, topics in blueprint:
        code = directory.make_chapter_code(board, grade, subject, ch_title)
        chapter = models.Chapter(
            chapter_code=code, board=board, grade=grade, subject=subject,
            unit=f"{subject} Unit",
            chapter_title=ch_title,
            chapter_display_name=f"{ch_title} ({code})",
            chapter_duration="3",
            chapter_description=f"Auto-seeded {board} Grade {grade} {subject} chapter.",
        )
        db.add(chapter)
        db.flush()

        for t_idx, (topic_title, concepts) in enumerate(topics, start=1):
            topic = models.Topic(
                chapter_id=chapter.id,
                topic_title=f"Topic {t_idx:02d}: {topic_title} ({code}_PL)",
                topic_display_name=topic_title,
                pre_post_learning="Post",
                topic_description=f"{topic_title} - seeded topic.",
            )
            db.add(topic)
            db.flush()

            for c_idx, (concept_title, details) in enumerate(concepts, start=1):
                slug = "".join(w[0] for w in concept_title.split())[:14]
                concept = models.Concept(
                    topic_id=topic.id,
                    concept_title=concept_title,
                    concept_display_name=f"{concept_title} ({code}_PL_{topic_title.replace(' ', '_')})",
                    concept_details=(
                        f"Description: {details} "
                        "// Types: Type 01: Standard application Case 01: introductory example "
                        "// Misconception: learners often confuse this with neighbouring ideas."
                    ),
                    keywords=", ".join(concept_title.lower().split()),
                )
                db.add(concept)
                db.flush()

                for g_type in ("Basic", "Intermediate", "Advanced"):
                    group = models.Group(
                        concept_id=concept.id, group_type=g_type,
                        group_name=f"({code}_PL_T{t_idx:02d}_{slug}) {g_type[:1]}G{c_idx:02d}",
                        group_display_name=f"{concept_title} - {g_type}",
                        group_description=f"{g_type} group for {concept_title}.",
                        group_status="Active",
                    )
                    db.add(group)
                    db.flush()

                    # One seed question per group, spread across sheet kinds.
                    kind = {"Basic": "objective", "Intermediate": "subjective",
                            "Advanced": "descriptive"}[g_type]
                    label = _label(code, t_idx, slug, g_type, c_idx)
                    q = models.Question(
                        group_id=group.id, sheet_kind=kind, question_label=label,
                        question_category={
                            "objective": "Multiple Choice Question",
                            "subjective": "Short Answer",
                            "descriptive": "Long Answer",
                        }[kind],
                        cognitive_skills={"Basic": "Remembering", "Intermediate": "Applying",
                                          "Advanced": "Analysing"}[g_type],
                        question_source="Aegis Seed",
                        level_of_difficulty={"Basic": "Less", "Intermediate": "Moderate",
                                             "Advanced": "High"}[g_type],
                        question=f"[{g_type}] Explain or apply: {concept_title}.",
                        marks={"objective": 1, "subjective": 3, "descriptive": 5}[kind],
                        answer_explanation=f"Refer to: {details}",
                        origin="seed",
                    )
                    if kind == "objective":
                        q.answers = [
                            {"answer_type": "Words", "answer_content": "Correct option",
                             "correct_answer": "Yes", "answer_weightage": "1"},
                            {"answer_type": "Words", "answer_content": "Distractor A",
                             "correct_answer": "No", "answer_weightage": "0"},
                        ]
                    elif kind == "subjective":
                        q.answers = [
                            {"answer_type": "Words", "answer": concept_title,
                             "answer_display": "Yes", "weightage": "3", "placeholder": "answer"},
                        ]
                    else:
                        q.display_answer = "Yes"
                        q.answers = [
                            {"answer_type": "Words", "answer_weightage": "5",
                             "answer_content": f"Full explanation of {concept_title}."},
                        ]
                        q.sub_questions = [
                            {"text": f"i. Define {concept_title}.", "marks": "2",
                             "keywords": [{"answer_type": "Words", "weightage": "2",
                                           "keyword": concept_title}]},
                            {"text": f"ii. Give an example of {concept_title}.", "marks": "3",
                             "keywords": [{"answer_type": "Words", "weightage": "3",
                                           "keyword": "worked example"}]},
                        ]
                    db.add(q)
    db.commit()


def main() -> None:
    engine = create_engine("sqlite://")  # in-memory
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        _seed(db)
        writer.write_workbook(db, dest=DEST)
        n = db.query(models.Question).count()
        print(f"wrote {DEST} ({n} seed questions across 3 sheets)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
