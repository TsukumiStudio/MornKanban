#!/usr/bin/env bash
# herdr-agent-worker.sh - KANBAN_WORKER_CMD / KANBAN_REVIEW_CMD wrapper.
# Runs each kanban worker as a VISIBLE interactive agent in its own Herdr
# pane instead of a headless `claude -p` / `codex exec` process, so every
# parallel card shows up as an agent in the Herdr sidebar and the user can
# watch or intervene. Requires the dispatcher itself to run inside a Herdr
# pane. Backend-aware: the launched agent's `--kind` (claude|codex) follows
# the card's own routing, same as kanban.sh's headless worker_cmd/review_cmd.
#
#   stdin  : card body (worker), review prompt (reviewer), or a conflict
#            resolution prompt (resolver)
#   cwd    : the card's worktree (kanban.sh cd's before invoking us)
#   stdout : agent transcript tail, recorded into the card History
#
#   KANBAN_HERDR_ROLE : "worker" (default), "reviewer", "resolver", or "operator"
#     (pane/agent name)
#   KANBAN_CARD_BACKEND / KANBAN_REVIEWER / KANBAN_RESOLVER : card's backend
#     routing, from kanban.sh (auto|claude|codex); "auto" resolves via
#     KANBAN_BACKEND_ORDER the same way kanban.sh's resolve_backend() does.
#   KANBAN_CARD_EFFORT : optional card-level effort shared by all roles.
set -euo pipefail

if [[ ${HERDR_ENV:-} != 1 ]]; then
  echo "herdr-agent-worker: HERDR_ENV != 1; the dispatcher must run inside a Herdr pane" >&2
  exit 1
fi

role=${KANBAN_HERDR_ROLE:-worker}
name=$(echo "${role}-$$-$RANDOM" | tr -cd 'a-z0-9_-' | cut -c1-31)
started_epoch=$(date +%s)
activity_log=${KANBAN_ACTIVITY_LOG:-}

log_activity() { # log_activity <event> <status>
  [[ -n $activity_log ]] || return 0
  python3 "$(cd "$(dirname "$0")" && pwd)/activity_log.py" "$activity_log" \
    --event "$1" --status "${2:-}" --card-id "${card_id:-unknown}" \
    --role "$role" --attempt "${attempt:-0}" --backend "${backend:-}" \
    --model "${model:-}" --effort "${effort:-}" --agent-name "$name" --pane-id "${pane:-}" \
    --duration-secs "$(($(date +%s) - started_epoch))" >/dev/null 2>&1 || true
}

infra_error() {
  log_activity infra_error "$1"
  printf 'KANBAN_INFRA_ERROR: %s: %s\n' "$1" "$2" >&2
  exit 1
}

resolve_auto_backend() { # echo first installed backend from KANBAN_BACKEND_ORDER
  local b
  for b in ${KANBAN_BACKEND_ORDER:-claude codex}; do
    if command -v "$b" >/dev/null 2>&1; then echo "$b"; return 0; fi
  done
  return 1
}

case $role in
  reviewer) backend_req=${KANBAN_REVIEWER:-auto} ;;
  resolver) backend_req=${KANBAN_RESOLVER:-auto} ;;
  *) backend_req=${KANBAN_CARD_BACKEND:-auto} ;;
esac

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
case $role in
  reviewer) model=${KANBAN_REVIEW_MODEL:-} ;;
  resolver) model=${KANBAN_RESOLVE_MODEL:-} ;;
  *) model=${KANBAN_CARD_MODEL:-} ;;
esac
if [[ $backend == claude && -z $model ]]; then model=sonnet; fi
effort=${KANBAN_CARD_EFFORT:-}

tmp=$(mktemp -d)
pane=""
pane_lock="${activity_log:-/tmp/mornkanban-${HERDR_TAB_ID:-tab}}.pane-layout.lock"
pane_lock_owned=false
cleanup() {
  if $pane_lock_owned; then rm -f "$pane_lock"; fi
  if [[ -n $pane ]]; then herdr pane close "$pane" >/dev/null 2>&1 || true; fi
  rm -rf "$tmp"
}
trap cleanup EXIT

cat >"$tmp/prompt.md"

if [[ $role == reviewer ]]; then
  cat >>"$tmp/prompt.md" <<'EOF'

無人実行契約: AskUserQuestion等で対話式の選択肢を表示しないこと。判断材料が不足する場合も質問せず、score 0 と不足情報をfeedbackへ書いた所定のJSONだけを返すこと。
EOF
else
  cat >>"$tmp/prompt.md" <<'EOF'

無人実行契約: AskUserQuestion等で対話式の選択肢を表示しないこと。タスクがworktree境界・project policy・必要なユーザー判断と衝突する場合は勝手に選択せず、作業を止め、最終回答本文の先頭行を `BLOCKED: <必要な判断と理由>` にすること。
EOF
fi

# Claude Code and Codex both render on the terminal's alternate screen, so a
# finished response cannot be recovered from pane scrollback. Have the agent
# also write its final answer to a file inside the worktree and read that.
# Bind the answer to the exact card, worktree, role, and attempt. Several
# visible agents can finish at nearly the same time, and a stale answer file
# must never be consumed by another attempt.
card_id=${KANBAN_CARD_ID:-unknown}
attempt=${KANBAN_CARD_ATTEMPT:-0}
worktree=$(basename "$PWD")
ident_line="KANBAN_ANSWER_ID: ${card_id}|${worktree}|${role}|${attempt}"
ans="$PWD/.kanban-answer.md"
rm -f "$ans"
printf '\n\n追加指示: 最終回答 (レビューなら JSON オブジェクトそのもの) を、チャット出力だけでなくファイル %s にも書き込むこと。ファイルの1行目は必ず次の識別行そのままにし、2行目以降に回答本体を続けること: %s\nこの書き込みが完了するまでは応答を終えないこと。作業・編集はカレントディレクトリ (worktree) 内だけで行い、リポジトリ本体のチェックアウトを絶対パスで触らないこと。\n' \
  "$ans" "$ident_line" >>"$tmp/prompt.md"

jget() { python3 -c 'import json,sys;d=json.load(sys.stdin);print(eval(sys.argv[1]))' "$1"; }

for _ in {1..200}; do
  if shlock -p $$ -f "$pane_lock"; then pane_lock_owned=true; break; fi
  sleep 0.05
done
$pane_lock_owned || infra_error pane_layout_failed "timed out waiting for the shared pane-layout lock"

layout_json=$(herdr pane layout --pane "${KANBAN_HERDR_DISPATCHER_PANE:-$HERDR_PANE_ID}") ||
  infra_error pane_layout_failed "could not read the Herdr pane layout"
panes_json=$(herdr pane list --workspace "${HERDR_WORKSPACE_ID}") ||
  infra_error pane_layout_failed "could not list Herdr panes"
plan=$(LAYOUT_JSON="$layout_json" PANES_JSON="$panes_json" python3 -c '
import json, os
layout = json.loads(os.environ["LAYOUT_JSON"])["result"]["layout"]
panes = json.loads(os.environ["PANES_JSON"]).get("result", {}).get("panes", [])
rects = {p["pane_id"]: p["rect"] for p in layout["panes"]}
secretary = os.environ.get("KANBAN_HERDR_SECRETARY_PANE", "")
dispatcher = os.environ.get("KANBAN_HERDR_DISPATCHER_PANE", os.environ["HERDR_PANE_ID"])
agents = [p for p in panes if p.get("label", "").startswith("kanban AI") and p.get("pane_id") in rects]
if not secretary or not agents:
    print(secretary or dispatcher, "right")
elif len(agents) == 1:
    agent_rect = rects[agents[0]["pane_id"]]
    print(dispatcher if agent_rect["y"] < rects[dispatcher]["y"] else secretary, "right")
else:
    target = max(agents, key=lambda p: (rects[p["pane_id"]]["height"], rects[p["pane_id"]]["width"]))
    print(target["pane_id"], "down")
') || infra_error pane_layout_failed "could not choose an AI pane position"
read -r target dir <<<"$plan"
if ! pane_json=$(herdr pane split --pane "$target" --direction "$dir" --cwd "$PWD" --no-focus); then
  infra_error pane_layout_failed "could not create an AI pane"
fi
pane=$(jget 'd["result"]["pane"]["pane_id"]' <<<"$pane_json") ||
  infra_error pane_layout_failed "could not read the new AI pane id"

# Label the pane so the user can tell WHO is doing WHAT at a glance. All
# roles run full-trust by default (see .kanban/KANBAN.md's worker/reviewer
# 権限ポリシー section) so the pane title says so plainly, not just via color.
label="kanban AI ${role} UNRESTRICTED ${backend}/${model:-unknown}/${effort:-unknown}: ${KANBAN_CARD_TITLE:-?}"
herdr pane rename "$pane" "$(echo "$label" | cut -c1-48)" >/dev/null 2>&1 ||
  infra_error pane_layout_failed "could not label the new AI pane"
rm -f "$pane_lock"
pane_lock_owned=false

# Start the interactive agent. A brand-new worktree triggers a folder-trust
# dialog, which surfaces as agent_not_ready; the worktree is our own
# checkout, so accept it and wait for idle.
# KANBAN_ALLOWED_TOOLS: extra pre-approved tools (e.g. "mcp__claude-in-chrome"
# for a browser-role card) so the unattended worker doesn't stall on the
# tool-permission dialog. Claude-specific flag; never passed to codex.
#
# Permission policy is uniform across worker/reviewer (resolver, once it
# exists, should reuse the same KANBAN_CLAUDE_PERMS/KANBAN_CODEX_* knobs):
# default is full-trust (no permission prompt, no sandbox/approval), driven
# by KANBAN_CLAUDE_PERMS / KANBAN_CODEX_SANDBOX / KANBAN_CODEX_FULL_BYPASS /
# KANBAN_CODEX_APPROVAL, which kanban.sh exports from .kanban/KANBAN.md
# frontmatter (env wins) when it invokes this wrapper as
# KANBAN_WORKER_CMD/KANBAN_REVIEW_CMD.
echo "herdr-agent-worker: [UNRESTRICTED] role=$role backend=$backend" >&2
kind_args=()
if [[ $backend == claude ]]; then
  perms=${KANBAN_CLAUDE_PERMS:-bypassPermissions}
  if [[ $perms == bypassPermissions ]]; then
    kind_args+=(--dangerously-skip-permissions)
  else
    kind_args+=(--permission-mode "$perms")
  fi
  [[ -n $model ]] && kind_args+=(--model "$model")
  [[ -n $effort ]] && kind_args+=(--effort "$effort")
  kind_args+=(--disallowedTools AskUserQuestion)
  if [[ -n ${KANBAN_ALLOWED_TOOLS:-} ]]; then kind_args+=(--allowedTools "$KANBAN_ALLOWED_TOOLS"); fi
else # codex
  if [[ ${KANBAN_CODEX_FULL_BYPASS:-true} == true ]]; then
    kind_args+=(--dangerously-bypass-approvals-and-sandbox)
  else
    kind_args+=(-s "${KANBAN_CODEX_SANDBOX:-danger-full-access}" -a "${KANBAN_CODEX_APPROVAL:-never}")
  fi
  [[ -n $model ]] && kind_args+=(-m "$model")
  [[ -n $effort ]] && kind_args+=(-c "model_reasoning_effort=$effort")
fi

# infra_error <category> <detail>: the caller (kanban.sh's
# classify_review_infra_error / classify_worker_infra_error) parses this
# sentinel to tell a broken pane/agent/wrapper from a genuine low review
# score or a genuinely empty worker diff, so it must be the first line of
# stdout and nothing else score-shaped should follow it.
start_error=""
if ! start_error=$(herdr agent start "$name" --kind "$backend" --pane "$pane" --timeout 45000 -- "${kind_args[@]}" 2>&1 >/dev/null); then
  local_started=false
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    ui=$(herdr agent read "$name" --source visible --lines 30 2>/dev/null || true)
    if grep -q "trust this folder" <<<"$ui"; then
      herdr agent send-keys "$name" down >/dev/null
      herdr agent send-keys "$name" enter >/dev/null
      local_started=true
      break
    fi
    sleep 2
  done
  if ! $local_started; then
    infra_error agent_start_failed "role=$role backend=$backend: ${start_error:-herdr agent start failed and no trust dialog was seen}"
  fi
  if ! wait_error=$(herdr agent wait "$name" --until idle --until done --timeout 60000 2>&1 >/dev/null); then
    infra_error agent_not_found "role=$role backend=$backend: agent never became ready after trust confirmation (${wait_error:-no detail})"
  fi
fi

prompt_agent() {
  local prompt_error
  if ! prompt_error=$(herdr agent prompt "$name" "$1" 2>&1 >/dev/null); then
    infra_error prompt_failed "role=$role card=$card_id: ${prompt_error:-herdr agent prompt failed}"
  fi
}

prompt_agent "$(cat "$tmp/prompt.md")"
log_activity agent_started running

# A blocked status is only a real permission/question dialog when the visible
# pane actually shows one; a long-running shell command (e.g. the worker's
# first `git log`) can otherwise transiently read as non-idle/blocked, and
# command output can innocently contain words like "permission" (e.g. "git:
# Permission denied"). Never send keys into a pane on a guess.
looks_like_permission_prompt() {
  grep -qE '(Do you want to (proceed|continue)\?|Allow (this|the) (action|command|tool)\?|\(y/n\)|\[y/N\]|Press enter to (confirm|continue)|don.t ask again)' <<<"$1"
}

looks_like_agent_question() {
  grep -qE '(Enter to select.*Esc to cancel|Type something|AskUserQuestion)' <<<"$1"
}

# idle/done is Herdr's *pane* status, not proof the agent finished writing
# its answer -- a transient idle/done blip (observed: reported ~20s into a
# still-running first command) must never be treated as completion by
# itself. Require the status to stay idle/done across several consecutive
# polls AND the answer file to exist and be byte-stable across a read gap
# before trusting it. If the agent is lost (status query fails, or reports
# the agent is gone) before that happens, that is an infrastructure error,
# not a completion.
answer_stable() {
  local f=$1 s1 s2
  [[ -s $f ]] || return 1
  s1=$(wc -c <"$f" 2>/dev/null) || return 1
  sleep "$STABLE_SLEEP"
  [[ -s $f ]] || return 1
  s2=$(wc -c <"$f" 2>/dev/null) || return 1
  [[ $s1 == "$s2" ]]
}

POLL_INTERVAL=${KANBAN_HERDR_POLL_INTERVAL:-3}
SETTLE_CHECKS=${KANBAN_HERDR_SETTLE_CHECKS:-2}
STABLE_SLEEP=${KANBAN_HERDR_STABLE_SLEEP:-2}
MAX_WAIT_SECS=${KANBAN_HERDR_ANSWER_WAIT_SECS:-${KANBAN_CARD_TIMEBOX_SECS:-1500}}
MISSING_ANSWER_GRACE_SECS=${KANBAN_HERDR_MISSING_ANSWER_GRACE_SECS:-60}
max_iters=$(python3 -c 'import math,sys; print(max(1, math.ceil(float(sys.argv[1]) / float(sys.argv[2]))))' "$MAX_WAIT_SECS" "$POLL_INTERVAL") ||
  infra_error wrapper_error "invalid poll/timeout settings: interval=$POLL_INTERVAL timeout=$MAX_WAIT_SECS"
missing_answer_grace_iters=$(python3 -c 'import math,sys; print(max(1, math.ceil(float(sys.argv[1]) / float(sys.argv[2]))))' "$MISSING_ANSWER_GRACE_SECS" "$POLL_INTERVAL") ||
  infra_error wrapper_error "invalid missing-answer grace: interval=$POLL_INTERVAL grace=$MISSING_ANSWER_GRACE_SECS"

settle_count=0
lost=0
answer_ready=0
answer_reprompted=0
answer_reprompt_iter=0
i=0
while ((i < max_iters)); do
  i=$((i + 1))
  get_out=$(herdr agent get "$name" 2>"$tmp/agent-get.err") || {
    get_err=$(tail -n 1 "$tmp/agent-get.err" 2>/dev/null || true)
    if grep -qiE "not found|no such (pane|agent)" <<<"$get_out"; then
      infra_error agent_not_found "role=$role: ${get_err:-agent was lost}"
    fi
    infra_error agent_not_found "role=$role: agent was lost (${get_err:-herdr agent get failed})"
  }
  st=$(jget 'd["result"]["agent"]["agent_status"]' <<<"$get_out" 2>/dev/null) || {
    infra_error wrapper_error "role=$role: could not parse agent status from herdr agent get"
  }
  case $st in
    idle | done)
      settle_count=$((settle_count + 1))
      ;;
    blocked)
      settle_count=0
      ui=$(herdr agent read "$name" --source visible --lines 40 2>/dev/null || true)
      if looks_like_permission_prompt "$ui"; then
        herdr agent send-keys "$name" enter >/dev/null 2>&1 || true
      elif looks_like_agent_question "$ui"; then
        infra_error agent_question "role=$role card=$card_id: agent opened an interactive choice instead of returning a non-interactive result"
      fi
      ;;
    gone | dead | missing | error)
      lost=1
      ;;
    *)
      settle_count=0
      ;;
  esac
  [[ $lost -eq 1 ]] && break

  if ((settle_count >= SETTLE_CHECKS)); then
    if [[ -f $ans ]] && answer_stable "$ans"; then
      answer_ready=1
      break
    fi
    if [[ ! -s $ans ]]; then
      if [[ $answer_reprompted -eq 0 ]]; then
        prompt_agent "作業本体は再実行せず、先ほどの最終回答ファイルの欠落だけを修復してください。$ans の1行目へ次の識別行をそのまま書き、2行目以降へ先ほどの最終回答（reviewerならJSONオブジェクトのみ）を書いてください: $ident_line"
        log_activity answer_reprompted running
        answer_reprompted=1
        answer_reprompt_iter=$i
        settle_count=0
      elif ((i - answer_reprompt_iter >= missing_answer_grace_iters)); then
        infra_error missing_answer "role=$role card=$card_id: agent settled twice without writing $ans, including after one focused recovery prompt"
      fi
    fi
  fi

  sleep "$POLL_INTERVAL"
done

if [[ $lost -eq 1 ]]; then
  infra_error agent_not_found "role=$role card=$card_id: agent was lost before writing $ans"
fi

if [[ $answer_ready -ne 1 ]]; then
  if [[ $answer_reprompted -eq 1 ]]; then
    infra_error missing_answer "role=$role card=$card_id: recovery prompt did not produce a stable $ans within ${MISSING_ANSWER_GRACE_SECS}s"
  fi
  if [[ ${KANBAN_CARD_KIND:-implementation} == diagnose ]]; then
    infra_error scope_timebox "role=$role card=$card_id: diagnosis hit its ${MAX_WAIT_SECS}s hard maximum before writing a stable answer"
  fi
  infra_error timeout "role=$role card=$card_id: timed out after ${MAX_WAIT_SECS}s waiting for a stable $ans"
fi

# The alternate-screen transcript is diagnostic context only, never a
# substitute for the answer file -- it is only ever emitted alongside a
# verified answer, never in place of one.
first_line=$(head -n 1 "$ans")
body=$(tail -n +2 "$ans")
rm -f "$ans"   # keep it out of the card's git commit / merge
if [[ $first_line != "$ident_line" ]]; then
  infra_error stale_answer "role=$role: answer identity mismatch (expected ${ident_line}; got ${first_line:0:120})"
fi
if [[ -z $body ]]; then
  infra_error empty_answer "role=$role: answer body was empty after a valid identity line"
fi
log_activity answer_accepted ok
echo ""
printf '%s\n' "$body"
