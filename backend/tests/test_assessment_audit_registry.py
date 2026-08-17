"""Assessment verdict provenance stays private to the release audit."""
from __future__ import annotations

from app.services import build_concepts_release as release


ASSESSMENT_AUDIT_FIELDS = {
    "_aegis_assessment_level_verdict",
    "_aegis_assessment_variant_cluster",
    "_aegis_assessment_group_description",
    "_aegis_assessment_group_quality",
}


def test_assessment_verdict_fields_use_the_shared_release_registry() -> None:
    record = {
        "concept_title": "Visible concept",
        **{
            field: {"decision_key": field}
            for field in ASSESSMENT_AUDIT_FIELDS
        },
    }

    assert ASSESSMENT_AUDIT_FIELDS <= release._RELEASE_AUDIT_FIELDS
    assert release._strip_release_fields(record) == {
        "concept_title": "Visible concept"
    }
