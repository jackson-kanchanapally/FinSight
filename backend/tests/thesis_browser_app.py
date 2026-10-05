"""Disposable file-backed SQLite app for concurrent Phase 8 browser requests."""
from pathlib import Path
from tempfile import TemporaryDirectory
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base, get_db
from tests.browser_app import app

scratch = TemporaryDirectory(prefix="almonds-thesis-browser-")
engine = create_engine(f"sqlite:///{Path(scratch.name) / 'test.db'}", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)


def isolated_db():
    with Session() as db:
        yield db


app.dependency_overrides[get_db] = isolated_db
