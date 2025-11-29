# ==== NEW: scripts/build_mac.sh ====
# Usage: bash scripts/build_mac.sh
# Output: dist/Crypto Watcher.app  (zip and send this to your friend)

set -euo pipefail

APP_NAME="Crypto Watcher"

# 1) Ensure deps
python3 -m pip install --upgrade pip
python3 -m pip install pyinstaller fastapi "uvicorn[standard]" httpx pydantic

# 2) Clean
rm -rf build dist .pytest_cache __pycache__ || true

# 3) Build .app (one-folder is most reliable on mac for frameworks)
pyinstaller \
  --noconfirm \
  --name "$APP_NAME" \
  --windowed \
  --add-data "static:static" \
  --hidden-import "uvicorn" \
  --hidden-import "anyio" \
  --hidden-import "starlette" \
  app.py

# 4) Zip for sharing
cd dist
zip -r "${APP_NAME}.zip" "${APP_NAME}.app"
echo "✅ Built dist/${APP_NAME}.app and ${APP_NAME}.zip"