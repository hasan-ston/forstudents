# Run this to activate the project's virtualenv and load nvm/node/npm
# Usage: source backend/activate-with-nvm.sh

export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck disable=SC1090
  . "$NVM_DIR/nvm.sh"
  # ensure the default node version is activated in this shell
  if command -v nvm >/dev/null 2>&1; then
    nvm use --silent default >/dev/null 2>&1 || true
  fi
fi

# Activate the virtualenv
if [ -f "$(pwd)/backend/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  . "$(pwd)/backend/.venv/bin/activate"
else
  echo "Cannot find backend/.venv/bin/activate — ensure the venv exists"
fi
