#!/usr/bin/env bash
# Regenerates the screenshot baselines. Only ever run this through the pinned Playwright container (run.sh), never with a local browser,
# on a stack loaded with the personas. Then look at `git diff --stat e2e/tests/__screenshots__` and open every changed image before committing:
# commit the baselines with the dashboard change that explains them, and say in the message which panels changed and why.
set -euo pipefail
cd "$(dirname "$0")"
E2E_WORKERS=1 exec ./run.sh --update-snapshots=all tests/screenshots.spec.ts
