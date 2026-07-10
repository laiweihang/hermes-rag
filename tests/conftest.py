import os
import tempfile
from pathlib import Path


_db_path = Path(tempfile.gettempdir()) / f"hermes-rag-tests-{os.getpid()}.db"
if _db_path.exists():
    _db_path.unlink()

os.environ["SQLALCHEMY_DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-at-least-32-bytes-long"
os.environ["ANONYMIZED_TELEMETRY"] = "False"
