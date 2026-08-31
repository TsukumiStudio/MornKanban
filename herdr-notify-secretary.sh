#!/usr/bin/env bash
# herdr-notify-secretary.sh - KANBAN_NOTIFY_CMD hook for Herdr environments.
# Called by the dispatcher as: <this> <done|failed> <card title>.
# Prompts the secretary agent so it reports card results itself instead of
# sitting idle after cutting cards. Never fails the dispatcher.
set -euo pipefail
state=${1:-?} title=${2:-?}
sec=${KANBAN_HERDR_SECRETARY:-secretary}
[[ ${HERDR_ENV:-} == 1 ]] || exit 0
# NOTE: ${title} 必須 — 直後の全角文字が変数名として解釈される (set -u で落ちる)
if [[ $state == failed ]]; then
  msg="カード「${title}」が failed になった。kanban show で原因を確認し、ユーザーへ報告して。"
else
  msg="カード「${title}」が done (マージ済み) になった。盤面が全て決着していれば結果を簡潔に報告して。"
fi
herdr agent prompt "$sec" "$msg" >/dev/null 2>&1 || true
