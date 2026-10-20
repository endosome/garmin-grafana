#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker info >/dev/null
if [[ ! -f .env ]]; then
  umask 077
  python3 - <<'PY'
from pathlib import Path
import secrets
s = Path('.env.example').read_text().replace('replace-with-a-long-random-password', secrets.token_urlsafe(32)).replace('replace-with-another-long-random-password', secrets.token_urlsafe(32)).replace('replace-with-a-reader-password', secrets.token_urlsafe(32))
Path('.env').write_text(s)
PY
fi
docker compose -f compose-example.yml build
docker compose -f compose-example.yml up -d postgres
docker compose -f compose-example.yml run --rm migrate
echo 'Login to Garmin; a one-week initial import runs before starting background services.'
initial_start=$(python3 -c 'from datetime import date,timedelta; print(date.today()-timedelta(days=7))')
docker compose -f compose-example.yml run --rm -e MANUAL_START_DATE="$initial_start" garmin-fetch-data
docker compose -f compose-example.yml up -d
echo 'Grafana: http://localhost:3000 (credentials in .env)'
