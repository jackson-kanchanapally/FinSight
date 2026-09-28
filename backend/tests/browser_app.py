"""Isolated API app for the frontend browser check.

Run only on localhost through frontend/scripts/browser-check.mjs. The in-memory
database is discarded when the server stops.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import settings
from app.database import Base, get_db
from app.main import app

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
