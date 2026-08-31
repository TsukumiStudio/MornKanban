#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required but was not found in PATH." >&2
  exit 1
fi

if python3 -c "import tkinter" 2>/dev/null; then
  exec python3 "$REPO/gui/setup_gui.py"
fi

if [ "$(uname)" = "Darwin" ] && command -v osascript >/dev/null 2>&1; then
  exec python3 "$REPO/gui/setup_osa.py"
fi

exec python3 "$REPO/gui/setup_cli.py"
