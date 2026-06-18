"""Live-only mode: dry stubs are opt-in (AEGIS_ALLOW_DRY=1, tests only)."""
import pytest

from app import config
from app.services import generation, mmd, workbooks


def test_live_required_when_dry_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("AEGIS_ALLOW_DRY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MATHPIX_APP_ID", raising=False)
    monkeypatch.delenv("MATHPIX_APP_KEY", raising=False)

    with pytest.raises(config.LiveRequiredError, match="OPENAI"):
        generation.generate_questions_for_concept(
            type("C", (), {
                "concept_title": "Angles",
                "concept_details": "x",
                "keywords": "",
                "topic": type("T", (), {
                    "topic_title": "Basics",
                    "chapter": type("Ch", (), {
                        "subject": "Mathematics",
                        "grade": "10",
                        "board": "CBSE",
                        "chapter_title": "Geometry",
                    })(),
                })(),
            })(),
            question_type="objective",
            cognitive_skill="Remember",
            difficulty="Less",
            category="Multiple Choice Question",
            count=1,
        )

    pdf = tmp_path / "chapter.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    with pytest.raises(config.LiveRequiredError, match="Mathpix"):
        mmd.to_mmd(pdf)

    with pytest.raises(config.LiveRequiredError, match="Workbooks"):
        workbooks.generate(
            tmp_path / "CBSE_NCERT_G08_CH04_QUADRILATERALS.pdf",
            "Mathematics",
        )


def test_allow_dry_permits_stub_generation(monkeypatch):
    monkeypatch.setenv("AEGIS_ALLOW_DRY", "1")
    monkeypatch.setenv("AEGIS_USE_LIVE", "0")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    rows = generation.identify_questions_from_mmd(
        "# Quiz\n\nWhat is pi?\n\nDefine radius.",
        upload_type="questions",
        question_type="auto",
    )
    assert rows
    assert rows[0]["sheet_kind"] == "objective"
