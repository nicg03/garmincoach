#!/bin/bash
# Double-click this file to launch the Garmin Sync desktop app.
# It uses the project's virtualenv, creating it on first run.
cd "$(dirname "$0")" || exit 1

if [ ! -d ".venv" ]; then
  echo "First run: setting up the environment (this happens once)…"
  python3 -m venv .venv || { echo "Could not create venv"; read -r; exit 1; }
  ./.venv/bin/pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -r requirements.txt || { echo "pip install failed"; read -r; exit 1; }
  ./.venv/bin/python -m playwright install chromium
fi

# Use the native Tk window if this Python has Tk; otherwise fall back to the
# zero-dependency local web UI (some Python builds ship without Tk).
if ./.venv/bin/python -c "import tkinter" >/dev/null 2>&1; then
  exec ./.venv/bin/python -m garmin_sync.app
else
  echo "Tk not available in this Python — opening the web interface instead."
  exec ./.venv/bin/python -m garmin_sync.webapp
fi
