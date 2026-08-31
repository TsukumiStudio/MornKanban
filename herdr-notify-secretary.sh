#!/usr/bin/env bash
# herdr-notify-secretary.sh - KANBAN_NOTIFY_CMD hook for Herdr environments.
# Called by the dispatcher as: <this> <done|failed|blocked> <card title>.
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
case $state in
  failed)
    msg="カード「${title}」が failed になった。failed は作業プロセスの失敗であり、製品の検証不合格とは限らない。kanban show で failure_kind と History を確認し、製品不具合・インフラ障害・未検証を区別してユーザーへ報告して。"
    ;;
  blocked)
    msg="カード「${title}」が blocked になった。kanban show で blocked_kind を確認して。dependency は依存先doneまで待機、review_infra は未検証としてユーザー判断が必要であり、デプロイ不可と推測しない。"
    ;;
  *)
    msg="カード「${title}」が done (マージ済み) になった。盤面が全て決着していれば結果を簡潔に報告して。"
    ;;
esac
if ! err=$(herdr agent prompt "$sec" "$msg" 2>&1 >/dev/null); then
  echo "herdr-notify-secretary: failed to notify '$sec': ${err:-herdr agent prompt failed}" >&2
  exit 1
fi
