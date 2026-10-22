"""Isolated API app for the frontend browser check.

Run only on localhost through frontend/scripts/browser-check.mjs. The temporary
database is discarded when the server stops.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import settings
from app.database import Base, get_db
from app.main import app

database_dir = TemporaryDirectory(prefix="almonds-browser-db-")
engine = create_engine(
    f"sqlite:///{Path(database_dir.name) / 'test.db'}", connect_args={"check_same_thread": False}
)
document_dir = TemporaryDirectory(prefix="almonds-browser-docs-")
settings.document_storage_path = Path(document_dir.name)
settings.event_source_mode = "fixture"
settings.event_monitor_enabled = False
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base.metadata.create_all(engine)


def override_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_db

# Browser fixtures own their test database; never start production background jobs.
from app.config import settings
settings.research_automation_enabled = False
