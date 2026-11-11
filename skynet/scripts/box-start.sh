#!/bin/sh
# Start Skynet inside an Amp Box release (see scripts/box-build.sh and .amp/box.yaml).
set -eu
DATA_DIR="${AMP_DEPLOYMENT_DATA_DIRECTORY:-$PWD/data}"
mkdir -p "$DATA_DIR/tmp"
export SKYNET_DATA_DIR="$DATA_DIR" TMPDIR="$DATA_DIR/tmp" PYTHONPATH="$PWD/pydeps"
export SKYNET_WORLD="$PWD/data/world.json"
CHROME=$(ls -d "$PWD"/browsers/chrome-headless-shell/*/chrome-headless-shell-linux64/chrome-headless-shell 2>/dev/null | head -n 1 || true)
[ -n "$CHROME" ] && export SKYNET_CHROME="$CHROME"
PY=$(ls -d "$PWD"/.py/cpython-3.12.*-linux-x86_64-gnu | head -n 1)/bin/python3.12
exec "$PY" -m uvicorn server.app:app --host 0.0.0.0 --port "${PORT:-8080}" --proxy-headers --forwarded-allow-ips '*'
