#!/bin/sh
cd "$(dirname "$0")" || exit 2
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10+ is required. See docs/FIRST_RUN.md."
  exit 2
fi
exec python3 scripts/start_workbench.py "$@"
