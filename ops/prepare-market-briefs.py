"""Stage/install the market-review addition while retaining installed Codex/inbox features."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path.home() / "Library/Application Support/Almonds"
STAGE = Path("/tmp/almonds-market-briefs-update")
NEW = ["backend/app/models/market_brief.py", "backend/app/schemas/market_brief.py",
       "backend/app/services/market_brief_sources.py", "backend/app/services/market_brief_analysis.py",
       "backend/app/services/market_briefs.py", "backend/app/api/market_briefs.py",
       "backend/alembic/versions/0028_market_briefs.py"]
MODIFIED = ["backend/app/config.py", "backend/app/main.py", "backend/app/models/__init__.py",
            "backend/app/services/telegram_alerts.py"]


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def once(value, old, new):
    if value.count(old) != 1:
        raise RuntimeError("Installed source differs from the reviewed market-brief insertion point")
    return value.replace(old, new, 1)


def section(value, start, end, replacement):
    if value.count(start) != 1:
        raise RuntimeError("Installed market-review section is ambiguous")
    old = start + value.split(start, 1)[1].split(end, 1)[0]
    return once(value, old, replacement)


def prepare():
    updating = (RUNTIME / NEW[-1]).exists()
    if updating and fingerprint(RUNTIME / NEW[-1]) != fingerprint(ROOT / NEW[-1]):
        raise RuntimeError("Installed market-review migration changed; use a new migration")
    prior = "backend/alembic/versions/0027_gold_alerts.py"
    if fingerprint(RUNTIME / prior) != fingerprint(ROOT / prior):
        raise RuntimeError("Installed Gold migration does not match the required predecessor")
    if STAGE.is_symlink():
        raise RuntimeError("Stage path must not be a symlink")
    if STAGE.exists():
        shutil.rmtree(STAGE)
    for relative in ("backend/app", "backend/alembic"):
        shutil.copytree(RUNTIME / relative, STAGE / relative, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(RUNTIME / "backend/alembic.ini", STAGE / "backend/alembic.ini")
    for relative in NEW:
        shutil.copy2(ROOT / relative, STAGE / relative)
    relative = "backend/app/config.py"
    installed, source = (RUNTIME / relative).read_text(), (ROOT / relative).read_text()
    fields = "    market_briefs_enabled:" + source.split("    market_briefs_enabled:", 1)[1].split("    telegram_bot_token:", 1)[0]
    installed = (section(installed, "    market_briefs_enabled:", "    telegram_bot_token:", fields) if updating else
                 once(installed, "    telegram_bot_token:", fields + "    telegram_bot_token:"))
    validator = '    @field_validator("market_brief_times_ist")' + source.split('    @field_validator("market_brief_times_ist")', 1)[1].split("    @property", 1)[0]
    installed = (section(installed, '    @field_validator("market_brief_times_ist")', "    @property", validator) if updating else
                 once(installed, "    @property\n    def database_url", validator + "    @property\n    def database_url"))
    (STAGE / relative).write_text(installed)
    relative = "backend/app/main.py"
    installed, source = (RUNTIME / relative).read_text(), (ROOT / relative).read_text()
    if not updating:
        installed = once(installed, "    gold,\n", "    gold,\n    market_briefs,\n")
    function = "async def scheduled_market_briefs():" + source.split("async def scheduled_market_briefs():", 1)[1].split("@asynccontextmanager", 1)[0]
    installed = (section(installed, "async def scheduled_market_briefs():", "@asynccontextmanager", function) if updating else
                 once(installed, "@asynccontextmanager", function + "@asynccontextmanager"))
    if not updating:
        installed = once(installed, "or settings.gold_monitor_enabled:\n", "or settings.gold_monitor_enabled or settings.market_briefs_enabled:\n")
    block = "        if settings.market_briefs_enabled:" + source.split("        if settings.market_briefs_enabled:", 1)[1].split("        if settings.gold_monitor_enabled:", 1)[0]
    installed = (section(installed, "        if settings.market_briefs_enabled:", "        if settings.gold_monitor_enabled:", block) if updating else
                 once(installed, "        if settings.gold_monitor_enabled:", block + "        if settings.gold_monitor_enabled:"))
    if not updating:
        installed = once(installed, "app.include_router(gold.router)", "app.include_router(gold.router)\napp.include_router(market_briefs.router)")
    (STAGE / relative).write_text(installed)
    relative = "backend/app/models/__init__.py"
    installed = (RUNTIME / relative).read_text()
    if not updating:
        installed += "\nfrom app.models.market_brief import MarketBrief, MarketBriefState\n"
    (STAGE / relative).write_text(installed)
    relative = "backend/app/services/telegram_alerts.py"
    source = (ROOT / relative).read_text()
    marker = '        if job.notification and job.notification.get("kind") == "MARKET_BRIEF":'
    gold = '        if job.notification and job.notification.get("kind") == "GOLD":'
    block = marker + source.split(marker, 1)[1].split(gold, 1)[0]
    installed = (RUNTIME / relative).read_text()
    (STAGE / relative).write_text(section(installed, marker, gold, block) if updating else once(installed, gold, block + gold))
    manifest = {relative: {"original": fingerprint(RUNTIME / relative), "prepared": fingerprint(STAGE / relative)}
                for relative in NEW + MODIFIED}
    (STAGE / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"Prepared {len(manifest)} market-review files at {STAGE}")


def bootstrap(service, definition):
    for _ in range(10):
        result = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(definition)], capture_output=True)
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("Could not restart the Almonds service")


def install():
    manifest = json.loads((STAGE / "manifest.json").read_text())
    if set(manifest) != set(NEW + MODIFIED):
        raise RuntimeError("Overlay file list differs from the reviewed addition")
    for relative, entry in manifest.items():
        if fingerprint(RUNTIME / relative) != entry["original"] or fingerprint(STAGE / relative) != entry["prepared"]:
            raise RuntimeError(f"Overlay changed after preparation: {relative}")
    backup = Path("/tmp") / ("almonds-before-market-briefs-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    for relative, entry in manifest.items():
        if entry["original"] is not None:
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(RUNTIME / relative, destination)
    shutil.copy2(STAGE / "manifest.json", backup / "manifest.json")
    service = f"gui/{os.getuid()}/com.almonds.portfolio"
    definition = Path.home() / "Library/LaunchAgents/com.almonds.portfolio.plist"
    subprocess.run(["launchctl", "bootout", service], capture_output=True, check=True)
    try:
        for relative in manifest:
            destination = RUNTIME / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(STAGE / relative, destination)
        bootstrap(service, definition)
        result = None
        for _ in range(40):
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/api/market-briefs", timeout=2) as response:
                    result = json.load(response)
                break
            except Exception:
                time.sleep(1)
        if result is None:
            raise RuntimeError("The market-review API did not become healthy")
    except Exception:
        subprocess.run(["launchctl", "bootout", service], capture_output=True)
        for relative, entry in manifest.items():
            if entry["original"] is not None:
                shutil.copy2(backup / relative, RUNTIME / relative)
        # Keep the additive migration/tables available if startup already applied it.
        bootstrap(service, definition)
        raise
    print(f"Market reviews installed; backup at {backup}")
    print({"enabled": result["enabled"], "telegram_connected": result["telegram_connected"], "schedule_ist": result["schedule_ist"]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    install() if args.install else prepare()
