#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required but was not found in PATH." >&2
  exit 1
fi

if [ ! -f "$REPO/gui/server.py" ]; then
  echo "Error: $REPO/gui/server.py not found." >&2
  exit 1
fi

PORT="${MORNKANBAN_GUI_PORT:-8765}"

if [ "${HERDR_ENV:-}" != "1" ]; then
  echo "Warning: Herdr のペイン内での起動を推奨 (秘書/ディスパッチャ起動ボタンが使えない)" >&2
fi

if command -v open >/dev/null 2>&1; then
  (sleep 1 && open "http://127.0.0.1:$PORT") &
fi

exec env MORNKANBAN_GUI_PORT="$PORT" python3 "$REPO/gui/server.py"
