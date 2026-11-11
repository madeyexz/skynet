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

# The Box (Ubuntu 24.04) has no Chrome libraries or fonts. Collect them from an Ubuntu 24.04
# container so they match the Box's glibc, and wrap Chrome to use them.
docker run --rm -v "$PWD/browsers:/b" -e HOST_ID="$(id -u):$(id -g)" ubuntu:24.04 sh -ec '
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libxkbcommon0 libasound2t64 libcups2t64 libpango-1.0-0 libcairo2 \
    libfontconfig1 fonts-dejavu-core >/dev/null
  C=$(ls /b/chrome-headless-shell/*/chrome-headless-shell-linux64/chrome-headless-shell)
  mkdir -p /b/lib /b/fonts
  ldd "$C" | awk "/=> \\//{print \$3}" | grep -vE "/(libc|libm|libdl|libpthread|librt|ld-linux[^/]*)\\.so" | xargs -I{} cp -L {} /b/lib/
  if ldd "$C" | grep -q "not found"; then ldd "$C" | grep "not found"; exit 1; fi
  cp -r /usr/share/fonts/truetype/dejavu /b/fonts/
  chown -R "$HOST_ID" /b'
cat > browsers/chrome <<'SH'
#!/bin/sh
D=$(cd "$(dirname "$0")" && pwd)
export LD_LIBRARY_PATH="$D/lib" FONTCONFIG_FILE="${SKYNET_FONTCONFIG:-$D/fonts.conf}"
exec "$D"/chrome-headless-shell/*/chrome-headless-shell-linux64/chrome-headless-shell "$@"
SH
chmod +x browsers/chrome
