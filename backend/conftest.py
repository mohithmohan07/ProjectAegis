import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Isolate tests from the developer's live database and data files.
os.environ.setdefault("AEGIS_DB_URL", f"sqlite:///{ROOT / 'aegis_test.db'}")
_test_data = Path(tempfile.mkdtemp(prefix="aegis_test_data_"))
os.environ.setdefault("AEGIS_DATA_DIR", str(_test_data))
# Keep tests on dummy fixtures — do not auto-load committed syllabus workbooks.
_empty_syllabus = _test_data / "bundled_syllabus"
_empty_syllabus.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("AEGIS_BUNDLED_SYLLABUS_DIR", str(_empty_syllabus))


# These seven assertions encode the product behavior this branch deliberately
# replaces: API generation automatically publishes to the database, fills
# publication-only chapter fields, or returns no output on failure. Keep them
# visible as strict expected failures until their source files are migrated.
# Replacement coverage is comprehensive in test_build_concepts_release.py.
_SUPERSEDED_AUTOMATIC_PUBLICATION_TESTS = frozenset({
    "tests/test_build_concepts.py::test_post_learning_creates_concepts",
    "tests/test_build_concepts.py::test_post_learning_groups_concepts_under_one_topic",
    (
        "tests/test_build_concepts.py::"
        "test_post_learning_api_discards_invalid_final_and_completes_retry_without_api"
    ),
    "tests/test_build_concepts.py::test_pre_learning_from_upload",
    "tests/test_concept_mapping_format.py::test_deposit_fills_required_fields",
    "tests/test_sources.py::test_concept_resused_across_books_merges_sources",
    "tests/test_uploads_flow.py::test_generate_requires_conversion",
})


def pytest_collection_modifyitems(items):
    marker = pytest.mark.xfail(
        strict=True,
        reason=(
            "Superseded by the unattended staged-release contract: generation "
            "no longer publishes to the database automatically, and failures "
            "release diagnostics instead of returning no output. Replacement "
            "coverage: tests/test_build_concepts_release.py."
        ),
    )
    for item in items:
        if item.nodeid in _SUPERSEDED_AUTOMATIC_PUBLICATION_TESTS:
            item.add_marker(marker)
