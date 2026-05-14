import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Use a per-process temp DB so tests never touch the developer's aegis.db.
os.environ.setdefault("AEGIS_DB_URL", f"sqlite:///{ROOT / 'aegis_test.db'}")
