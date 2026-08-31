#!/usr/bin/env bash
# herdr-agent-worker.sh - KANBAN_WORKER_CMD / KANBAN_REVIEW_CMD wrapper.
# Runs each kanban worker as a VISIBLE interactive agent in its own Herdr
# pane instead of a headless `claude -p` / `codex exec` process, so every
# parallel card shows up as an agent in the Herdr sidebar and the user can
# watch or intervene. Requires the dispatcher itself to run inside a Herdr
# pane. Backend-aware: the launched agent's `--kind` (claude|codex) follows
# the card's own routing, same as kanban.sh's headless worker_cmd/review_cmd.
#
#   stdin  : card body (worker) or review prompt (reviewer)
#   cwd    : the card's worktree (kanban.sh cd's before invoking us)
#   stdout : agent transcript tail, recorded into the card History
#
#   KANBAN_HERDR_ROLE : "worker" (default) or "reviewer" (pane/agent name)
#   KANBAN_CARD_BACKEND / KANBAN_REVIEWER : card's backend routing, from
#     kanban.sh (auto|claude|codex); "auto" resolves via KANBAN_BACKEND_ORDER
#     the same way kanban.sh's resolve_backend() does.
set -euo pipefail

if [[ ${HERDR_ENV:-} != 1 ]]; then
  echo "herdr-agent-worker: HERDR_ENV != 1; the dispatcher must run inside a Herdr pane" >&2
  exit 1
fi

role=${KANBAN_HERDR_ROLE:-worker}
name=$(echo "${role}-$$-$RANDOM" | tr -cd 'a-z0-9_-' | cut -c1-31)

resolve_auto_backend() { # echo first installed backend from KANBAN_BACKEND_ORDER
  local b
  for b in ${KANBAN_BACKEND_ORDER:-claude codex}; do
    if command -v "$b" >/dev/null 2>&1; then echo "$b"; return 0; fi
  done
  return 1
}

if [[ $role == reviewer ]]; then
  backend_req=${KANBAN_REVIEWER:-auto}
else
  backend_req=${KANBAN_CARD_BACKEND:-auto}
fi

if [[ $backend_req == auto ]]; then
  backend=$(resolve_auto_backend) ||
    { echo "herdr-agent-worker: no agent CLI found for auto backend (role: $role, order: ${KANBAN_BACKEND_ORDER:-claude codex})" >&2; exit 1; }
else
  backend=$backend_req
fi

case $backend in
  claude|codex) ;;
  *) echo "herdr-agent-worker: unsupported backend '$backend' (role: $role; claude|codex only)" >&2; exit 1 ;;
esac
command -v "$backend" >/dev/null 2>&1 ||
  { echo "herdr-agent-worker: backend '$backend' CLI not found in PATH (role: $role)" >&2; exit 1; }

# Model policy: top-tier models are reserved for the secretary/design roles.
# Hands-on Claude workers/reviewers default to sonnet unless the card or
# KANBAN_REVIEW_MODEL says otherwise. Codex has its own default model and
# must never be handed a Claude model name (or vice versa), so an empty
# codex model is left empty rather than defaulted.
if [[ $role == reviewer ]]; then
  model=${KANBAN_REVIEW_MODEL:-}
else
  model=${KANBAN_CARD_MODEL:-}
fi
if [[ $backend == claude && -z $model ]]; then model=sonnet; fi

tmp=$(mktemp -d)
pane=""
cleanup() {
  if [[ -n $pane ]]; then herdr pane close "$pane" >/dev/null 2>&1 || true; fi
  rm -rf "$tmp"
}
trap cleanup EXIT

cat >"$tmp/prompt.md"

# Claude Code and Codex both render on the terminal's alternate screen, so a
# finished response cannot be recovered from pane scrollback. Have the agent
# also write its final answer to a file inside the worktree and read that.
ans="$PWD/.kanban-answer.md"
rm -f "$ans"
printf '\n\n追加指示: 最終回答 (レビューなら JSON オブジェクトそのもの) を、チャット出力だけでなくファイル %s にもそのまま書き込むこと。作業・編集はカレントディレクトリ (worktree) 内だけで行い、リポジトリ本体のチェックアウトを絶対パスで触らないこと。\n' "$ans" >>"$tmp/prompt.md"

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
label="${role} (${backend}): ${KANBAN_CARD_TITLE:-?}"
herdr pane rename "$pane" "$(echo "$label" | cut -c1-48)" >/dev/null 2>&1 || true

# Start the interactive agent. A brand-new worktree triggers a folder-trust
# dialog, which surfaces as agent_not_ready; the worktree is our own
# checkout, so accept it and wait for idle.
# KANBAN_ALLOWED_TOOLS: extra pre-approved tools (e.g. "mcp__claude-in-chrome"
# for a browser-role card) so the unattended worker doesn't stall on the
# tool-permission dialog. Claude-specific flag; never passed to codex.
kind_args=()
if [[ $backend == claude ]]; then
  if [[ $role != reviewer ]]; then kind_args+=(--permission-mode acceptEdits); fi
  [[ -n $model ]] && kind_args+=(--model "$model")
  if [[ -n ${KANBAN_ALLOWED_TOOLS:-} ]]; then kind_args+=(--allowedTools "$KANBAN_ALLOWED_TOOLS"); fi
else # codex
  if [[ $role == reviewer ]]; then
    kind_args+=(-s read-only -a never)
  else
    kind_args+=(-s "${KANBAN_CODEX_SANDBOX:-workspace-write}" -a never)
  fi
  [[ -n $model ]] && kind_args+=(-m "$model")
fi

if ! herdr agent start "$name" --kind "$backend" --pane "$pane" --timeout 45000 -- "${kind_args[@]}" >/dev/null 2>&1; then
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
