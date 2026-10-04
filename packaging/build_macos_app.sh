#!/bin/bash
# Build SheetFlow.app for the Mac this script is running on.
# A Windows installer has to be built on Windows; this script does not produce one.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64) TRIPLE="aarch64-apple-darwin" ;;
  x86_64) TRIPLE="x86_64-apple-darwin" ;;
  *)
    echo "Unsupported architecture: $ARCH" >&2
    exit 1
    ;;
esac

APP="$ROOT/dist/SheetFlow.app"
CACHE="$ROOT/packaging/build"
mkdir -p "$CACHE"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/backend" "$APP/Contents/Resources/frontend"

echo "Building the dashboard"
(cd "$ROOT/frontend" && npm install && npm run build)
mkdir -p "$APP/Contents/Resources/frontend"
rm -rf "$APP/Contents/Resources/frontend/dist"
cp -R "$ROOT/frontend/dist" "$APP/Contents/Resources/frontend/dist"

echo "Downloading a relocatable Python 3.13 for $TRIPLE"
ASSET_URL="$(
  curl -fsSL "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest" |
    TRIPLE="$TRIPLE" python3 -c '
import json, os, sys
triple = os.environ["TRIPLE"]
needle = f"{triple}-install_only.tar.gz"
assets = json.load(sys.stdin)["assets"]
matches = [
    item["browser_download_url"]
    for item in assets
    if item["name"].endswith(needle) and item["name"].startswith("cpython-3.13") and "freethreaded" not in item["name"]
]
if not matches:
    sys.exit("No Python 3.13 build was found for " + triple)
print(matches[0])
'
)"
TARBALL="$CACHE/$(basename "$ASSET_URL")"
if [[ ! -f "$TARBALL" ]]; then
  curl -fL "$ASSET_URL" -o "$TARBALL"
fi
rm -rf "$APP/Contents/Resources/python"
tar -xzf "$TARBALL" -C "$APP/Contents/Resources"
"$APP/Contents/Resources/python/bin/python3" -m pip install --upgrade pip
"$APP/Contents/Resources/python/bin/python3" -m pip install -r "$ROOT/backend/requirements.txt"

echo "Copying the application"
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$ROOT/backend/app" "$APP/Contents/Resources/backend/"
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$ROOT/backend/alembic" "$APP/Contents/Resources/backend/"
cp "$ROOT/backend/alembic.ini" "$APP/Contents/Resources/backend/alembic.ini"
cp "$ROOT/packaging/launcher.py" "$APP/Contents/Resources/launcher.py"
cp "$ROOT/packaging/macos/Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT/packaging/macos/sheetflow" "$APP/Contents/MacOS/sheetflow"
chmod +x "$APP/Contents/MacOS/sheetflow"

codesign --force --deep --sign - "$APP"
echo "Built $APP"
