import os
import sys
import tempfile
from pathlib import Path

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
