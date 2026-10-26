# Local macOS service

The optional macOS launcher runs the API and built dashboard at `http://127.0.0.1:8000` under the logged-in user's LaunchAgent. It is intended for a single trusted local user. The API includes unauthenticated endpoints; do not expose it to a network without adding access controls.

## Install or update

From the repository root, after completing the [local setup](../README.md#local-setup) and building the frontend:

```sh
cd frontend && npm run build && cd ..
python3 ops/install-production.py
```

The installer copies the app, virtual environment, Compose configuration and `.env` to `~/Library/Application Support/Almonds`, then creates `~/Library/LaunchAgents/com.almonds.portfolio.plist`. It sets the copied `.env` permissions to owner-only. Review the installer before running it and confirm its paths and database settings suit your machine.

The service starts at login and restarts after a crash. Docker Desktop must be running, and the Mac must be awake with the user logged in. Logs and downloaded documents are stored under the Application Support directory. Back up the database volume and document directory before removing or replacing them.

## Control the service

```sh
launchctl kickstart -k gui/$(id -u)/com.almonds.portfolio
launchctl bootout gui/$(id -u)/com.almonds.portfolio
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.almonds.portfolio.plist"
```

The launcher binds to loopback (`127.0.0.1`), applies database migrations, and starts one API worker. This setup is macOS-specific; for other environments, run the API and database using the development instructions or adapt the deployment to your platform and security requirements.
