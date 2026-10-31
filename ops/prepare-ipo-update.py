"""Prepare a narrow IPO overlay for the newer installed Almonds runtime.

Default: prepare /tmp/almonds-ipo-update without touching the installation.
--install: apply that reviewed overlay, backing up changed files and restarting
the existing login service. No credentials or environment files are copied.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path.home() / "Library/Application Support/Almonds"
STAGE = Path("/tmp/almonds-ipo-update")
NEW = ["backend/app/api/ipos.py", "backend/app/models/ipo.py", "backend/app/schemas/ipo.py",
       "backend/app/services/ipo_sources.py", "backend/app/services/ipos.py",
       "backend/alembic/versions/0026_mainboard_ipos.py"]
MODIFIED = ["backend/app/config.py", "backend/app/main.py", "backend/app/models/__init__.py",
            "backend/app/models/telegram.py", "backend/app/services/telegram_alerts.py"]


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def replace_once(value, old, new):
    if value.count(old) != 1:
        raise RuntimeError("Installed source differs from the reviewed insertion point")
    return value.replace(old, new, 1)


def prepare():
    if (RUNTIME / "backend/alembic/versions/0026_mainboard_ipos.py").exists():
        raise RuntimeError("IPO update already installed; review the existing overlay before preparing another")
    for relative in ("backend/app", "backend/alembic"):
        shutil.copytree(RUNTIME / relative, STAGE / relative, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(RUNTIME / "backend/alembic.ini", STAGE / "backend/alembic.ini")
    if not (RUNTIME / "backend/alembic/versions/0025_telegram_queries.py").exists():
        raise RuntimeError("This overlay is for the installation with Telegram-query migration 0025")
    for relative in NEW:
        shutil.copy2(ROOT / relative, STAGE / relative)

    relative = "backend/app/config.py"
    # Merge settings against this checkout's base, preserving installed Codex and
    # Telegram-query options. Conflicts require reviewing the source, never a reset.
    with tempfile.NamedTemporaryFile() as base:
        base.write(subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT))
        base.flush()
        result = subprocess.run(["git", "merge-file", "-p", str(ROOT / relative), base.name,
                                 str(RUNTIME / relative)], capture_output=True)
        if result.returncode:
            raise RuntimeError("Review the installed settings merge before installing")
        (STAGE / relative).write_bytes(result.stdout)

    relative = "backend/app/main.py"
    installed = (RUNTIME / relative).read_text()
    ours = (ROOT / relative).read_text()
    installed = replace_once(installed, "    reports,\n", "    reports,\n    ipos,\n")
    function = "async def scheduled_ipos():" + ours.split("async def scheduled_ipos():", 1)[1].split("@asynccontextmanager", 1)[0]
    installed = replace_once(installed, "@asynccontextmanager", function + "@asynccontextmanager")
    installed = replace_once(installed, "or settings.telegram_enabled:\n", "or settings.telegram_enabled or settings.ipo_monitor_enabled:\n")
    scheduler = '        scheduler = AsyncIOScheduler(timezone="UTC")\n'
    addition = ours.split(scheduler, 1)[1].split("        if settings.telegram_enabled:", 1)[0]
    installed = replace_once(installed, scheduler, scheduler + addition)
    installed = replace_once(installed, "app.include_router(reports.router)", "app.include_router(reports.router)\napp.include_router(ipos.router)")
    (STAGE / relative).write_text(installed)

    relative = "backend/app/models/__init__.py"
    (STAGE / relative).write_text((RUNTIME / relative).read_text() + "\nfrom app.models.ipo import IPOIssue, IPOCheck\n")
    relative = "backend/app/models/telegram.py"
    installed = (RUNTIME / relative).read_text()
    installed = replace_once(installed,
        'event_id: Mapped[int] = mapped_column(\n        ForeignKey("company_events.id", ondelete="CASCADE"), nullable=False)',
        'event_id: Mapped[int | None] = mapped_column(\n        ForeignKey("company_events.id", ondelete="CASCADE"), nullable=True)')
    (STAGE / relative).write_text(installed)

    relative = "backend/app/services/telegram_alerts.py"
    marker = "        if job is None or job.lease_token != token:\n            return None\n"
    block = (ROOT / relative).read_text().split(marker, 1)[1].split("        event = db.get(CompanyEvent, job.event_id)", 1)[0]
    if "validate_delivery" not in block:
        raise RuntimeError("IPO delivery block missing")
    (STAGE / relative).write_text(replace_once((RUNTIME / relative).read_text(), marker, marker + block))
    shutil.copytree(ROOT / "frontend/dist", STAGE / "frontend/dist", dirs_exist_ok=True)
    files = NEW + MODIFIED + [str(p.relative_to(STAGE)) for p in (STAGE / "frontend/dist").rglob("*") if p.is_file()]
    manifest = {"original": {f: fingerprint(RUNTIME / f) for f in files},
                "prepared": {f: fingerprint(STAGE / f) for f in files}}
    (STAGE / "ipo-overlay.json").write_text(json.dumps(manifest, indent=2))
    print(f"Prepared IPO update at {STAGE}; existing Telegram and Codex features preserved")


def install():
    manifest = json.loads((STAGE / "ipo-overlay.json").read_text())
    for relative, original in manifest["original"].items():
        if fingerprint(RUNTIME / relative) != original:
            raise RuntimeError(f"Installed file changed after preparation: {relative}")
        if fingerprint(STAGE / relative) != manifest["prepared"][relative]:
            raise RuntimeError(f"Prepared file changed after review: {relative}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/tmp") / f"almonds-before-ipos-{stamp}"
    for relative, original in manifest["original"].items():
        if original is not None:
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(RUNTIME / relative, target)
    shutil.copy2(STAGE / "ipo-overlay.json", backup / "ipo-overlay.json")
    service = f"gui/{os.getuid()}/com.almonds.portfolio"
    definition = Path.home() / "Library/LaunchAgents/com.almonds.portfolio.plist"
    subprocess.run(["launchctl", "bootout", service], check=True, capture_output=True)
    try:
        for relative in manifest["prepared"]:
            target = RUNTIME / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(STAGE / relative, target)
    finally:
        # bootout may return before launchd has released the old job completely.
        for attempt in range(10):
            result = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(definition)], capture_output=True)
            if result.returncode == 0:
                break
            if attempt == 9:
                raise RuntimeError(f"Service restart failed; changed files are backed up at {backup}")
            time.sleep(2)
    print(f"Installed IPO update; prior changed files backed up at {backup}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    install() if args.install else prepare()
