#!/usr/bin/env bash
# herdr-notify-secretary.sh - KANBAN_NOTIFY_CMD hook for Herdr environments.
# Called by the dispatcher as: <this> <done|failed> <card title>.
# Prompts the secretary agent so it reports card results itself instead of
# sitting idle after cutting cards. Never fails the dispatcher.
set -euo pipefail
state=${1:-?} title=${2:-?}
REPO="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=kanban-root.sh
source "$REPO/kanban-root.sh"
[[ ${HERDR_ENV:-} == 1 ]] || exit 0
sec=${KANBAN_HERDR_SECRETARY:-}
if [[ -z $sec ]]; then
  # kanban-secretary.sh dispatch always exports KANBAN_HERDR_SECRETARY into
  # the dispatcher pane, so this only matters for a manual/standalone
  # invocation - resolve the same name the same way bootstrap/dispatch did,
  # from the project root of $PWD (the dispatcher's own cwd). Never fatal.
  root=$(kanban_project_root "$PWD" 2>/dev/null) || root=""
  if [[ -n $root ]]; then
    sec=$(python3 "$REPO/registry/cli.py" secretary resolve "$root" 2>/dev/null |
      python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])' 2>/dev/null || true)
  fi
  [[ -n $sec ]] || exit 0
fi
# NOTE: ${title} 必須 — 直後の全角文字が変数名として解釈される (set -u で落ちる)
if [[ $state == failed ]]; then
  msg="カード「${title}」が failed になった。kanban show で原因を確認し、ユーザーへ報告して。"
else
  msg="カード「${title}」が done (マージ済み) になった。盤面が全て決着していれば結果を簡潔に報告して。"
fi
if ! err=$(herdr agent prompt "$sec" "$msg" 2>&1 >/dev/null); then
  echo "herdr-notify-secretary: failed to notify '$sec': ${err:-herdr agent prompt failed}" >&2
  exit 1
fi
