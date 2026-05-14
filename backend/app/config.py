import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", ROOT / "data"))
DB_URL = os.environ.get("AEGIS_DB_URL", f"sqlite:///{ROOT / 'aegis.db'}")

DATA_DIR.mkdir(parents=True, exist_ok=True)
