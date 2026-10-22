"""Install/restart Almonds' login service on this Mac, using the existing DB.

Run after building frontend/dist and verifying the application tests.
"""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    if not (root / "frontend/dist/index.html").is_file():
        raise SystemExit("Build the dashboard first: cd frontend && npm run build")
    uid = os.getuid()
    service = f"gui/{uid}/com.almonds.portfolio"
    # Stop the previous snapshot before replacing code/dependencies beneath it.
    subprocess.run(["launchctl", "bootout", service], capture_output=True)
    runtime = Path.home() / "Library/Application Support/Almonds"
    runtime.mkdir(parents=True, exist_ok=True)
    runtime.chmod(0o700)
    # launchd cannot read a Desktop workspace under macOS privacy controls.
    # Copy the verified runtime, dependencies and content-addressed documents;
    # keep the same PostgreSQL Compose project and relative document references.
    for relative in ("backend/app", "backend/alembic", "backend/scripts",
                     "backend/.venv", "frontend/dist", "ops"):
        source = root / relative
        destination = runtime / relative
        destination.mkdir(parents=True, exist_ok=True)
        subprocess.run(["/usr/bin/rsync", "-a", "--delete", "--exclude=__pycache__",
                        str(source) + "/", str(destination) + "/"], check=True)
    for relative in (".env", "docker-compose.yml", "backend/alembic.ini"):
        shutil.copy2(root / relative, runtime / relative)
    (runtime / ".env").chmod(0o600)
    documents = runtime / "backend/data/documents"
    documents.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/usr/bin/rsync", "-a", str(root / "backend/data/documents") + "/",
                    str(documents) + "/"], check=True)
    logs = runtime / "backend/data/runtime"
    logs.mkdir(parents=True, exist_ok=True)
    target = Path.home() / "Library/LaunchAgents/com.almonds.portfolio.plist"
    target.parent.mkdir(parents=True, exist_ok=True)
    definition = {
        "Label": "com.almonds.portfolio",
        "ProgramArguments": ["/bin/bash", str(runtime / "ops/run-production.sh")],
        "WorkingDirectory": str(runtime),
        "EnvironmentVariables": {
            "COMPOSE_PROJECT_NAME": root.name.lower(),
            "DOCUMENT_STORAGE_PATH": str(documents),
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "production.log"),
        "StandardErrorPath": str(logs / "production-error.log"),
    }
    temporary = target.with_suffix(".plist.tmp")
    temporary.write_bytes(plistlib.dumps(definition))
    temporary.chmod(0o600)
    temporary.replace(target)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(target)], check=True)
    print(f"Installed {target}")
    print("Dashboard and API: http://127.0.0.1:8000")
    print(f"Logs: {logs}")


if __name__ == "__main__":
    main()
