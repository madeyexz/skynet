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
npx -y @puppeteer/browsers@2 install chrome-headless-shell@stable --path browsers >/dev/null
