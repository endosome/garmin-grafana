#!/usr/bin/env bash
# Runs the browser tests in the pinned Playwright image, inside a memory limit, with two workers by default.
# Usage: e2e/run.sh [playwright test arguments]   (GRAFANA_URL, KIOSK_URL, GRAFANA_PASSWORD from the environment)
# The stack must be up with the personas loaded; see docs/testing.md.
set -euo pipefail
cd "$(dirname "$0")"
version=$(sed -n 's/.*"@playwright\/test": "\([^"]*\)".*/\1/p' package.json)
exec docker run --rm --network host --ipc host --memory "${E2E_MEMORY:-3g}" --cpus 2 --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e GRAFANA_URL -e KIOSK_URL -e GRAFANA_ALLOW_WRITES -e GRAFANA_USER -e GRAFANA_PASSWORD -e CI -e KIOSK_DASHBOARD -e E2E_WORKERS \
  -v "$PWD/..":/repo -w /repo/e2e "mcr.microsoft.com/playwright:v${version}-noble" \
  bash -c 'npm ci --no-audit --no-fund --loglevel=error && npx playwright test --workers="${E2E_WORKERS:-2}" "$@"' -- "$@"
