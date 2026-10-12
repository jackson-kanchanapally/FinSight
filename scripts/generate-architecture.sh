#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
archify_bin="$repo_root/.agents/skills/archify/bin/archify.mjs"
latest="$repo_root/.archify/latest"
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
run_receipts="$latest/validation/$run_id"

cd "$repo_root"
python3 .archify/tools/inventory.py

export ARCHIFY_UPDATE_CHECK_DISABLED="${ARCHIFY_UPDATE_CHECK_DISABLED:-1}"
mac_playwright_chrome="${HOME}/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
if [[ -z "${ARCHIFY_CHROME:-}" && -x "$mac_playwright_chrome" ]]; then
  export ARCHIFY_CHROME="$mac_playwright_chrome"
fi

for candidate in "$latest"/candidates/*.json; do
  slug="$(basename "$candidate" .json)"
  node "$archify_bin" finalize architecture "$candidate" "$latest/$slug.html" \
    --repo-root "$repo_root" --quality showcase --json \
    --out-dir "$run_receipts/$slug"
done

echo "Architecture regenerated. Validation receipts: $run_receipts"
echo "Open $latest/system-overview.html"
