"""Prepare or install a narrow gold overlay for the installed Almonds app.

Default prepares /tmp/almonds-gold-update for review. --install verifies its
manifest, backs up replaced files, and restarts the existing login service.
The installed private environment and newer Telegram/Codex features are retained.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path.home() / "Library/Application Support/Almonds"
STAGE = Path("/tmp/almonds-gold-update")
NEW = ["backend/app/api/gold.py", "backend/app/models/gold.py", "backend/app/schemas/gold.py",
       "backend/app/services/gold.py", "backend/app/services/gold_sources.py", "backend/app/services/gold_reasoning.py",
       "backend/alembic/versions/0027_gold_alerts.py"]
MODIFIED = ["backend/app/config.py", "backend/app/main.py", "backend/app/models/__init__.py",
            "backend/app/services/telegram_alerts.py"]


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def replace_once(value, old, new):
    if value.count(old) != 1:
        raise RuntimeError("Installed source differs from the verified gold insertion point")
    return value.replace(old, new, 1)


def prepare():
    if (RUNTIME / NEW[-1]).exists():
        raise RuntimeError("Gold is already installed; prepare a reviewed incremental update instead")
    for relative in ("backend/app", "backend/alembic"):
        shutil.copytree(RUNTIME / relative, STAGE / relative, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(RUNTIME / "backend/alembic.ini", STAGE / "backend/alembic.ini")
    for relative in NEW:
        shutil.copy2(ROOT / relative, STAGE / relative)

    relative = "backend/app/config.py"
    installed, ours = (RUNTIME / relative).read_text(), (ROOT / relative).read_text()
    installed = replace_once(installed, "from pydantic import Field, SecretStr\n",
                            "from pydantic import Field, SecretStr, field_validator\n")
    if not re.search(r"^from datetime import datetime$", installed, re.MULTILINE):
        installed = replace_once(installed, "from pathlib import Path\n", "from pathlib import Path\nfrom datetime import datetime\n")
    fields = "    gold_monitor_enabled:" + ours.split("    gold_monitor_enabled:", 1)[1].split("    telegram_bot_token:", 1)[0]
    installed = replace_once(installed, "    telegram_bot_token:", fields + "    telegram_bot_token:")
    validator = '    @field_validator("gold_alert_times_ist")' + ours.split('    @field_validator("gold_alert_times_ist")', 1)[1].split("    @property", 1)[0]
    installed = replace_once(installed, "    @property\n    def database_url", validator + "    @property\n    def database_url")
    (STAGE / relative).write_text(installed)

    relative = "backend/app/main.py"
    installed, ours = (RUNTIME / relative).read_text(), (ROOT / relative).read_text()
    installed = replace_once(installed, "    ipos,\n", "    ipos,\n    gold,\n")
    function = "async def scheduled_gold():" + ours.split("async def scheduled_gold():", 1)[1].split("@asynccontextmanager", 1)[0]
    installed = replace_once(installed, "@asynccontextmanager", function + "@asynccontextmanager")
    installed = replace_once(installed, "or settings.ipo_monitor_enabled:\n",
                            "or settings.ipo_monitor_enabled or settings.gold_monitor_enabled:\n")
    block = "        if settings.gold_monitor_enabled:" + ours.split("        if settings.gold_monitor_enabled:", 1)[1].split("        if settings.ipo_monitor_enabled:", 1)[0]
    installed = replace_once(installed, "        if settings.ipo_monitor_enabled:", block + "        if settings.ipo_monitor_enabled:")
    installed = replace_once(installed, "app.include_router(ipos.router)", "app.include_router(ipos.router)\napp.include_router(gold.router)")
    (STAGE / relative).write_text(installed)

    relative = "backend/app/models/__init__.py"
    (STAGE / relative).write_text((RUNTIME / relative).read_text() + "\nfrom app.models.gold import GoldState, GoldSnapshot, GoldHeadline\n")
    relative = "backend/app/services/telegram_alerts.py"
    ours = (ROOT / relative).read_text()
    block = '        if job.notification and job.notification.get("kind") == "GOLD":' + ours.split('        if job.notification and job.notification.get("kind") == "GOLD":', 1)[1].split('        if job.notification and job.notification.get("kind") == "IPO":', 1)[0]
    marker = '        if job.notification and job.notification.get("kind") == "IPO":'
    (STAGE / relative).write_text(replace_once((RUNTIME / relative).read_text(), marker, block + marker))

    bundle = ROOT / "frontend/dist"
    if not any("Gold watch" in path.read_text() for path in (bundle / "assets").glob("*.js")):
        raise RuntimeError("Build the gold frontend before preparing the update")
    shutil.copytree(bundle, STAGE / "frontend/dist", dirs_exist_ok=True)
    files = NEW + MODIFIED + [str(path.relative_to(STAGE)) for path in (STAGE / "frontend/dist").rglob("*") if path.is_file()]
    manifest = {"original": {name: fingerprint(RUNTIME / name) for name in files},
                "prepared": {name: fingerprint(STAGE / name) for name in files}}
    (STAGE / "gold-overlay.json").write_text(json.dumps(manifest, indent=2))
    print(f"Prepared {len(files)} gold update files at {STAGE}")


def restart_service(service, definition):
    for attempt in range(10):
        result = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(definition)], capture_output=True)
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("Could not restart the Almonds login service")


def prepare_incremental():
    if not (RUNTIME / NEW[-1]).exists():
        raise RuntimeError("Gold must already be installed before preparing an incremental update")
    if fingerprint(RUNTIME / NEW[-1]) != fingerprint(ROOT / NEW[-1]):
        raise RuntimeError("An installed migration changed; prepare a new migration instead")
    for relative in ("backend/app", "backend/alembic"):
        shutil.copytree(RUNTIME / relative, STAGE / relative, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(RUNTIME / "backend/alembic.ini", STAGE / "backend/alembic.ini")
    for relative in NEW:
        shutil.copy2(ROOT / relative, STAGE / relative)
    relative = "backend/app/config.py"
    installed, ours = (RUNTIME / relative).read_text(), (ROOT / relative).read_text()
    if not re.search(r"^from datetime import datetime$", installed, re.MULTILINE):
        installed = replace_once(installed, "from pathlib import Path\n", "from pathlib import Path\nfrom datetime import datetime\n")
    end = "    market_briefs_enabled:" if "    market_briefs_enabled:" in installed else "    telegram_bot_token:"
    old_fields = "    gold_monitor_enabled:" + installed.split("    gold_monitor_enabled:", 1)[1].split(end, 1)[0]
    fields = "    gold_monitor_enabled:" + ours.split("    gold_monitor_enabled:", 1)[1].split("    market_briefs_enabled:", 1)[0]
    installed = replace_once(installed, old_fields, fields)
    start = '    @field_validator("gold_alert_times_ist")'
    end = '    @field_validator("market_brief_times_ist")' if '    @field_validator("market_brief_times_ist")' in installed else "    @property"
    old_validator = start + installed.split(start, 1)[1].split(end, 1)[0]
    validator = start + ours.split(start, 1)[1].split('    @field_validator("market_brief_times_ist")', 1)[0]
    (STAGE / relative).write_text(replace_once(installed, old_validator, validator))
    relative = "backend/app/main.py"
    installed, ours = (RUNTIME / relative).read_text(), (ROOT / relative).read_text()
    start, end = "        if settings.gold_monitor_enabled:", "        if settings.ipo_monitor_enabled:"
    old_scheduler = start + installed.split(start, 1)[1].split(end, 1)[0]
    scheduler = start + ours.split(start, 1)[1].split(end, 1)[0]
    (STAGE / relative).write_text(replace_once(installed, old_scheduler, scheduler))
    bundle = ROOT / "frontend/dist"
    if not any("Indian rate location" in path.read_text() for path in (bundle / "assets").glob("*.js")):
        raise RuntimeError("Build the India/Hyderabad frontend before preparing the update")
    if (STAGE / "frontend/dist").exists():
        shutil.rmtree(STAGE / "frontend/dist")
    shutil.copytree(bundle, STAGE / "frontend/dist")
    files = NEW + ["backend/app/config.py", "backend/app/main.py"] + [str(path.relative_to(STAGE)) for path in (STAGE / "frontend/dist").rglob("*") if path.is_file()]
    manifest = {"original": {name: fingerprint(RUNTIME / name) for name in files},
                "prepared": {name: fingerprint(STAGE / name) for name in files}}
    (STAGE / "gold-overlay.json").write_text(json.dumps(manifest, indent=2))
    print(f"Prepared {len(files)} incremental gold files at {STAGE}; installed Codex/inbox and market-review settings retained")


def wait_for_gold():
    for attempt in range(40):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/api/gold", timeout=2) as response:
                return json.load(response)
        except Exception:
            time.sleep(1)
    raise RuntimeError("The gold API did not become healthy after restart")


def install():
    manifest = json.loads((STAGE / "gold-overlay.json").read_text())
    if set(manifest["original"]) != set(manifest["prepared"]):
        raise RuntimeError("Overlay manifest file lists differ")
    for relative, original in manifest["original"].items():
        allowed = relative in NEW + MODIFIED or relative.startswith("frontend/dist/")
        if not allowed or ".." in Path(relative).parts or Path(relative).is_absolute():
            raise RuntimeError("Unsafe overlay manifest path")
        if fingerprint(RUNTIME / relative) != original:
            raise RuntimeError(f"Installed file changed after preparation: {relative}")
        if fingerprint(STAGE / relative) != manifest["prepared"][relative]:
            raise RuntimeError(f"Prepared file changed after review: {relative}")
    backup = Path("/tmp") / ("almonds-before-gold-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    for relative, original in manifest["original"].items():
        if original is not None:
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(RUNTIME / relative, target)
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(STAGE / "gold-overlay.json", backup / "gold-overlay.json")
    # Begin the new event policy now rather than replaying earlier feed history.
    config_path = "backend/app/config.py"
    if ("gold_major_event_alerts_since" in (STAGE / config_path).read_text() and
            "gold_major_event_alerts_since" not in (RUNTIME / config_path).read_text()):
        environment = RUNTIME / ".env"
        content = environment.read_text()
        if not re.search(r"^\s*(?:export\s+)?GOLD_MAJOR_EVENT_ALERTS_SINCE\s*=", content, re.MULTILINE):
            cutoff = datetime.now(timezone.utc).isoformat()
            descriptor = os.open(environment, os.O_WRONLY | os.O_APPEND)
            try:
                os.write(descriptor, ("\nGOLD_MAJOR_EVENT_ALERTS_SINCE=" + cutoff + "\n").encode())
            finally:
                os.close(descriptor)
    service = f"gui/{os.getuid()}/com.almonds.portfolio"
    definition = Path.home() / "Library/LaunchAgents/com.almonds.portfolio.plist"
    subprocess.run(["launchctl", "bootout", service], check=True, capture_output=True)
    try:
        for relative in manifest["prepared"]:
            target = RUNTIME / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(STAGE / relative, target)
        restart_service(service, definition)
        result = wait_for_gold()
    except Exception:
        subprocess.run(["launchctl", "bootout", service], capture_output=True)
        # Keep the additive migration and new tables available if it already ran.
        # Restoring prior integration files disables gold without losing history.
        for relative, original in manifest["original"].items():
            if original is not None:
                shutil.copy2(backup / relative, RUNTIME / relative)
        restart_service(service, definition)
        raise
    print(f"Gold installed; prior files backed up at {backup}")
    print(f"Monitoring enabled: {result['status']['enabled']}; Telegram connected: {result['status']['telegram_connected']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install", action="store_true")
    mode.add_argument("--update", action="store_true", help="Prepare an incremental update to installed Gold")
    args = parser.parse_args()
    install() if args.install else prepare_incremental() if args.update else prepare()
