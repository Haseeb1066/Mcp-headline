#!/usr/bin/env bash
# Native Render build: Python deps + (re)build Vite UI when Node is available.
set -euo pipefail

pip install -r requirements.txt

if [[ -f dist/web/index.html ]]; then
  echo "Found committed dist/web — UI ready."
fi

# Rebuild UI when Node is available (keeps deploy fresh)
if command -v node >/dev/null 2>&1 || [[ ! -f dist/web/index.html ]]; then
  if ! command -v node >/dev/null 2>&1; then
    export NVM_DIR="${HOME}/.nvm"
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
    # shellcheck disable=SC1091
    . "${NVM_DIR}/nvm.sh"
    nvm install 20
    nvm use 20
  fi
  npm ci
  npm run build
fi

test -f dist/web/index.html || {
  echo "ERROR: dist/web/index.html missing after build" >&2
  exit 1
}
