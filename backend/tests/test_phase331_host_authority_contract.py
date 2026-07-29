"""Regression coverage for authoritative Phase 3.3 Type-host routing."""
from __future__ import annotations

import pytest

from app.services import generation as g


def _concepts() -> dict[str, dict]:
    return {
        "CONCEPT-0001": {
            "concept_id": "CONCEPT-0001",
            "topic": "The Making of Nationalism in Europe",
            "parent_concept": "National Identity",
            "concept": "Shared National Identity",
            "is_culmination": False,
        },
        "CONCEPT-0002": {
            "concept_id": "CONCEPT-0002",
            "topic": "The Making of Nationalism in Europe",
            "parent_concept": "Economic Foundations of Nationalism",
            "concept": "Economic Integration through the Zollverein",
            "is_culmination": False,
        },
    }


def test_verified_type_host_overrides_token_overlap_guess():
    unit = {
        "type_title": "National integration",
        "task_pattern": "Explain integration among German states.",
        "topic_match_hint": "The Making of Nationalism in Europe",
        "parent_concept_match_hint": "Economic Foundations of Nationalism",
        "concept_match_hint": "Economic Integration through the Zollverein",
        "_phase33_host_verified": True,
    }

    selected = g._high_confidence_assignment_override(
        unit,
        ("CONCEPT-0001", "CONCEPT-0002"),
        _concepts(),
    )

    assert selected == "CONCEPT-0002"


def test_missing_verified_host_fails_closed():
    unit = {
        "topic_match_hint": "The Making of Nationalism in Europe",
        "parent_concept_match_hint": "Economic Foundations of Nationalism",
        "concept_match_hint": "A Missing Verified Concept",
        "_phase33_host_verified": True,
    }

    with pytest.raises(RuntimeError, match="verified Type host is absent"):
        g._high_confidence_assignment_override(
            unit,
            ("CONCEPT-0001", "CONCEPT-0002"),
            _concepts(),
        )
