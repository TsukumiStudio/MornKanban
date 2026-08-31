#!/usr/bin/env bash
# herdr-agent-worker.sh - KANBAN_WORKER_CMD / KANBAN_REVIEW_CMD wrapper.
# Runs each kanban worker as a VISIBLE interactive agent in its own Herdr
# pane instead of a headless `claude -p` process, so every parallel card
# shows up as an agent in the Herdr sidebar and the user can watch or
# intervene. Requires the dispatcher itself to run inside a Herdr pane.
#
#   stdin  : card body (worker) or review prompt (reviewer)
#   cwd    : the card's worktree (kanban.sh cd's before invoking us)
#   stdout : agent transcript tail, recorded into the card History
#
#   KANBAN_HERDR_ROLE : "worker" (default) or "reviewer" (pane/agent name)
set -euo pipefail

if [[ ${HERDR_ENV:-} != 1 ]]; then
  echo "herdr-agent-worker: HERDR_ENV != 1; the dispatcher must run inside a Herdr pane" >&2
  exit 1
fi

role=${KANBAN_HERDR_ROLE:-worker}
name=$(echo "${role}-$$-$RANDOM" | tr -cd 'a-z0-9_-' | cut -c1-31)

# Model policy: top-tier models are reserved for the secretary/design roles.
# Hands-on workers and reviewers default to sonnet unless the card or
# KANBAN_REVIEW_MODEL says otherwise.
if [[ $role == reviewer ]]; then
  model=${KANBAN_REVIEW_MODEL:-sonnet}
else
  model=${KANBAN_CARD_MODEL:-}
  [[ -n $model ]] || model=sonnet
fi

tmp=$(mktemp -d)
pane=""
cleanup() {
  if [[ -n $pane ]]; then herdr pane close "$pane" >/dev/null 2>&1 || true; fi
  rm -rf "$tmp"
}
trap cleanup EXIT

cat >"$tmp/prompt.md"

# Claude Code renders on the terminal's alternate screen, so a finished
# response cannot be recovered from pane scrollback. Have the agent also
# write its final answer to a file inside the worktree and read that.
ans="$PWD/.kanban-answer.md"
rm -f "$ans"
printf '\n\n追加指示: 最終回答 (レビューなら JSON オブジェクトそのもの) を、チャット出力だけでなくファイル %s にもそのまま書き込むこと。\n' "$ans" >>"$tmp/prompt.md"

jget() { python3 -c 'import json,sys;d=json.load(sys.stdin);print(eval(sys.argv[1]))' "$1"; }

# Split along the longer visual axis (terminal cells are ~2:1 tall, so a
# pane is "wide" when width exceeds twice its row count). Stacking every
# worker downward makes rows unusably short.
dir=$(herdr pane layout --current | python3 -c '
import json, os, sys
lay = json.load(sys.stdin)["result"]["layout"]
me = os.environ.get("HERDR_PANE_ID", "")
for p in lay["panes"]:
    if p["pane_id"] == me:
        r = p["rect"]
        print("right" if r["width"] > 2 * r["height"] else "down")
        break
else:
    print("down")')
pane=$(herdr pane split --current --direction "$dir" --cwd "$PWD" --no-focus |
  jget 'd["result"]["pane"]["pane_id"]')

# Label the pane so the user can tell WHO is doing WHAT at a glance.
label="${role}: ${KANBAN_CARD_TITLE:-?}"
herdr pane rename "$pane" "$(echo "$label" | cut -c1-48)" >/dev/null 2>&1 || true

# Start the interactive agent. A brand-new worktree triggers Claude's
# folder-trust dialog, which surfaces as agent_not_ready; the worktree is
# our own checkout, so accept it and wait for idle.
if ! herdr agent start "$name" --kind claude --pane "$pane" --timeout 45000 -- --permission-mode acceptEdits --model "$model" >/dev/null 2>&1; then
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    ui=$(herdr agent read "$name" --source visible --lines 30 2>/dev/null || true)
    if grep -q "trust this folder" <<<"$ui"; then
      herdr agent send-keys "$name" down >/dev/null
      herdr agent send-keys "$name" enter >/dev/null
      break
    fi
    sleep 2
  done
  herdr agent wait "$name" --timeout 60000 >/dev/null
fi

herdr agent prompt "$name" "$(cat "$tmp/prompt.md")" --wait --timeout 1500000 >/dev/null 2>&1 || true

# Ride out permission prompts (approve: these are our own sandboxed
# worktrees) and keep waiting until the agent settles for good.
for _ in $(seq 1 90); do
  st=$(herdr agent get "$name" | jget 'd["result"]["agent"]["agent_status"]')
  case $st in
    idle|done) break ;;
    blocked)
      ui=$(herdr agent read "$name" --source visible --lines 40 2>/dev/null || true)
      if grep -qE "Do you want|Allow|permission" <<<"$ui"; then
        herdr agent send-keys "$name" enter >/dev/null 2>&1 || true
      fi
      sleep 5 ;;
    *) herdr agent wait "$name" --timeout 300000 >/dev/null 2>&1 || true ;;
  esac
done

herdr agent read "$name" --source recent-unwrapped --lines 200 --format text 2>/dev/null || true
if [[ -f $ans ]]; then
  echo ""
  cat "$ans"
  rm -f "$ans"   # keep it out of the card's git commit / merge
fi
