"""Single-origin local deployment of the existing API and built dashboard."""
from pathlib import Path

from starlette.staticfiles import StaticFiles

from app.main import app

dashboard = Path(__file__).resolve().parents[2] / "frontend" / "dist"
app.mount("/", StaticFiles(directory=dashboard, html=True), name="dashboard")
