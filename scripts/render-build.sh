#!/usr/bin/env bash
# Native Render build: Python deps + Vite UI (Node only needed at build time).
set -euo pipefail

pip install -r requirements.txt

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
