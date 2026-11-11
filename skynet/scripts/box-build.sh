#!/bin/sh
# Build a self-contained Skynet release for an Amp Box: a relocatable CPython, the pip
# dependencies in ./pydeps, and Chrome's headless shell in ./browsers (for PDF records).
# The Box runs the release read-only, as an unprivileged user with no apt.
set -eu
uv python install 3.12 --install-dir .py
rm -f .py/.gitignore .py/cpython-3.12-linux-x86_64-gnu   # uv adds a broken minor-version link here
PY=$(ls -d .py/cpython-3.12.*-linux-x86_64-gnu | head -n 1)/bin/python3.12
uv export --quiet --frozen --no-hashes --no-dev --no-emit-project -o .requirements.txt
uv pip install --quiet --python "$PY" --target pydeps -r .requirements.txt
# Chrome for Testing's headless shell (the @puppeteer/browsers installer fails to unzip on new Node)
CFT=https://storage.googleapis.com/chrome-for-testing-public
VER=$(curl -fsS https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json \
  | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["channels"]["Stable"]["version"])')
rm -rf browsers && mkdir -p browsers/chrome-headless-shell/linux-$VER
curl -fsSL -o browsers/chs.zip "$CFT/$VER/linux64/chrome-headless-shell-linux64.zip"
unzip -q browsers/chs.zip -d browsers/chrome-headless-shell/linux-$VER && rm browsers/chs.zip
test -x browsers/chrome-headless-shell/linux-$VER/chrome-headless-shell-linux64/chrome-headless-shell
