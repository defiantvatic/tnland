#!/usr/bin/env bash
# One-command start: creates a virtualenv on first run, then launches.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run - creating a virtual environment and installing dependencies."
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip --quiet
  ./.venv/bin/python -m pip install -r requirements.txt --quiet
  echo "Setup complete."
fi
exec ./.venv/bin/python -m tnland "$@"
