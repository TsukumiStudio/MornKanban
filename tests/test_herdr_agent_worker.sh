#!/usr/bin/env bash
# test_herdr_agent_worker.sh - scenario tests for herdr-agent-worker.sh's
# worker/reviewer/resolver completion detection against a mock `herdr` CLI.
#
# No real Herdr, Claude, or Codex is touched. Each scenario drives the
# real herdr-agent-worker.sh script (unmodified) against a scripted mock
# `herdr` binary that simulates one failure mode from the bug report:
# a transient idle/done blip while the agent is still working must never
# be read as completion, a running shell must never be mistaken for a real
# permission prompt, and an agent that never produces an answer (timeout or
# disappearance) must end in a clear infrastructure error, never a
# success-shaped stdout.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WORKER="$REPO/herdr-agent-worker.sh"

fail_count=0
pass_count=0

note() { printf '%s\n' "$*" >&2; }
ok() { pass_count=$((pass_count + 1)); note "  ok - $1"; }
bad() { fail_count=$((fail_count + 1)); note "  FAIL - $1"; }

assert_eq() { # assert_eq <label> <actual> <expected>
  if [[ $2 == "$3" ]]; then ok "$1"; else bad "$1 (got [$2], want [$3])"; fi
}
assert_contains() { # assert_contains <label> <haystack> <needle>
  if [[ $2 == *"$3"* ]]; then ok "$1"; else bad "$1 (missing [$3] in [$2])"; fi
}
assert_not_contains() {
  if [[ $2 != *"$3"* ]]; then ok "$1"; else bad "$1 (unexpectedly found [$3] in [$2])"; fi
}

# --- mock herdr -------------------------------------------------------------
# Each scenario gets its own SCEN dir holding:
#   statuses       one `herdr agent get` agent_status reply per line (last
#                  line repeats once exhausted); a line of "FAIL" makes that
#                  call fail outright (simulates the agent process vanishing)
#   ui.blocked     text `herdr agent read --source visible` returns while blocked
#   get_calls      call counter / log (one line appended per `agent get`)
#   sendkeys.log   one line per `herdr agent send-keys ... enter`
#   close.log      one line per `herdr pane close`
#   on_call_N      optional: sourced (as a shell snippet) right before the
#                  Nth `agent get` reply is produced, so a scenario can mutate
#                  the answer file mid-run the same way a real agent would
make_mock_bin() {
  local bindir=$1
  mkdir -p "$bindir"
  cat >"$bindir/claude" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$bindir/claude"
  cp "$bindir/claude" "$bindir/codex"

  cat >"$bindir/herdr" <<'MOCKEOF'
#!/usr/bin/env bash
set -euo pipefail
scen=${MOCK_SCEN_DIR:?MOCK_SCEN_DIR not set}

case "$1 $2" in
  "pane layout")
    cat <<JSON
{"result":{"layout":{"panes":[{"pane_id":"self","rect":{"width":10,"height":10}}]}}}
JSON
    ;;
  "pane split")
    echo '{"result":{"pane":{"pane_id":"child-pane"}}}'
    ;;
  "pane rename") exit 0 ;;
  "pane close")
    echo "close $3" >>"$scen/close.log"
    exit 0
    ;;
  "agent start") exit 0 ;;
  "agent prompt") exit 0 ;;
  "agent wait") exit 0 ;;
  "agent send-keys")
    echo "sendkeys $3 $4" >>"$scen/sendkeys.log"
    exit 0
    ;;
  "agent read")
    # distinguish --source visible (blocked-prompt UI) vs the final
    # recent-unwrapped diagnostic tail
    if [[ " $* " == *" visible "* ]]; then
      cat "$scen/ui.blocked" 2>/dev/null || true
    else
      echo "diagnostic transcript tail"
    fi
    exit 0
    ;;
  "agent get")
    n=$(($(wc -l <"$scen/get_calls" 2>/dev/null || echo 0) + 1))
    echo "$n" >>"$scen/get_calls"
    if [[ -f "$scen/on_call_$n" ]]; then
      # shellcheck disable=SC1090
      source "$scen/on_call_$n"
    fi
    total=$(wc -l <"$scen/statuses")
    idx=$n
    if ((idx > total)); then idx=$total; fi
    st=$(sed -n "${idx}p" "$scen/statuses")
    if [[ $st == FAIL ]]; then
      exit 7
    fi
    echo "{\"result\":{\"agent\":{\"agent_status\":\"$st\"}}}"
    ;;
  *)
    echo "mock herdr: unhandled command: $*" >&2
    exit 1
    ;;
esac
MOCKEOF
  chmod +x "$bindir/herdr"
}

run_worker() { # run_worker <scen-dir> <worktree-dir> [role] [backend] -> stdout in $OUT, exit code in $RC, stderr in $ERR
  local scen=$1 wt=$2 role=${3:-worker} backend=${4:-claude}
  set +e
  OUT=$(cd "$WT" && env \
    HERDR_ENV=1 HERDR_PANE_ID=self \
    KANBAN_CARD_ID=test-card KANBAN_CARD_ATTEMPT=attempt-1 \
    KANBAN_HERDR_ROLE="$role" \
    KANBAN_CARD_KIND="${MOCK_CARD_KIND:-implementation}" \
    KANBAN_BACKEND_ORDER="$backend" KANBAN_CARD_BACKEND="$backend" \
    KANBAN_REVIEWER="$backend" KANBAN_RESOLVER="$backend" \
    KANBAN_HERDR_POLL_INTERVAL=0.1 KANBAN_HERDR_SETTLE_CHECKS=2 \
    KANBAN_HERDR_STABLE_SLEEP=0.05 KANBAN_HERDR_ANSWER_WAIT_SECS="${MOCK_MAX_WAIT:-3}" \
    KANBAN_ACTIVITY_LOG="$scen/activity.jsonl" \
    MOCK_SCEN_DIR="$scen" \
    PATH="$scen/bin:$PATH" \
    "$WORKER" <<<"card body" 2>"$scen/stderr.log")
  RC=$?
  set -e
  ERR=$(cat "$scen/stderr.log")
}

# Sets globals SCEN and WT (must not run in a subshell/command-substitution,
# or those assignments would be invisible to the caller).
new_scenario() {
  SCEN=$(mktemp -d)
  make_mock_bin "$SCEN/bin"
  WT="$SCEN/wt"
  mkdir -p "$WT"
}

# --- scenario 1: transient idle -> re-working -> answer written -----------
test_transient_idle_then_reworking_then_answer() {
  note "scenario: transient idle blip, agent keeps working, answer arrives late"
  new_scenario
  printf 'idle\nworking\nworking\nidle\nidle\n' >"$SCEN/statuses"
  cat >"$SCEN/on_call_4" <<EOF
printf 'KANBAN_ANSWER_ID: test-card|wt|worker|attempt-1\nFINAL ANSWER CONTENT\n' > "$WT/.kanban-answer.md"
EOF
  run_worker "$SCEN" "$WT"
  assert_eq "exits 0 once the answer actually lands" "$RC" "0"
  assert_contains "stdout carries the real answer" "$OUT" "FINAL ANSWER CONTENT"
  assert_contains "correlation log records the card" "$(cat "$SCEN/activity.jsonl")" '"card_id":"test-card"'
  assert_contains "correlation log records pane and agent lifecycle" "$(cat "$SCEN/activity.jsonl")" '"event":"answer_accepted"'
  local calls
  calls=$(wc -l <"$SCEN/get_calls")
  if ((calls >= 4)); then
    ok "did not stop at the first transient idle (call 1); polled through call $calls"
  else
    bad "stopped too early after only $calls status poll(s) -- the transient idle blip closed the pane like the reported bug"
  fi
  [[ ! -f "$SCEN/sendkeys.log" ]] && ok "no keys sent (never actually blocked)" || bad "unexpectedly sent keys: $(cat "$SCEN/sendkeys.log")"
  rm -rf "$SCEN"
}

# --- scenario 2: blocked status during a running shell must not be treated
#     as a real permission prompt (false positive on "permission" in output)
test_blocked_false_positive_running_shell() {
  note "scenario: 'blocked' status while a shell is merely running (git failure text)"
  new_scenario
  printf 'blocked\nblocked\nidle\nidle\n' >"$SCEN/statuses"
  cat >"$SCEN/ui.blocked" <<'EOF'
$ git push
remote: Permission denied (publickey).
fatal: Could not read from remote repository.
EOF
  cat >"$SCEN/on_call_3" <<EOF
printf 'KANBAN_ANSWER_ID: test-card|wt|worker|attempt-1\nANSWER-2\n' > "$WT/.kanban-answer.md"
EOF
  run_worker "$SCEN" "$WT"
  assert_eq "still completes successfully" "$RC" "0"
  assert_contains "stdout carries the real answer" "$OUT" "ANSWER-2"
  if [[ -f "$SCEN/sendkeys.log" ]]; then
    bad "sent a key into a pane that only showed running-shell/error text: $(cat "$SCEN/sendkeys.log")"
  else
    ok "never pressed Enter on a false-positive 'permission' match"
  fi
  rm -rf "$SCEN"
}

# --- scenario 3: a genuine permission/question dialog IS approved ---------
test_blocked_true_permission_prompt() {
  note "scenario: 'blocked' status with a real permission dialog on screen"
  new_scenario
  printf 'blocked\nidle\nidle\n' >"$SCEN/statuses"
  cat >"$SCEN/ui.blocked" <<'EOF'
Allow the action?
> 1. Yes
  2. No, and tell Claude what to do differently (y/n)
EOF
  cat >"$SCEN/on_call_2" <<EOF
printf 'KANBAN_ANSWER_ID: test-card|wt|worker|attempt-1\nANSWER-3\n' > "$WT/.kanban-answer.md"
EOF
  run_worker "$SCEN" "$WT"
  assert_eq "completes successfully" "$RC" "0"
  assert_contains "stdout carries the real answer" "$OUT" "ANSWER-3"
  if [[ -f "$SCEN/sendkeys.log" ]]; then
    local n
    n=$(wc -l <"$SCEN/sendkeys.log" | tr -d ' ')
    assert_eq "pressed Enter exactly once for the real prompt" "$n" "1"
  else
    bad "never approved a genuine permission dialog"
  fi
  rm -rf "$SCEN"
}

# --- scenario 4: idle forever, answer never written -> bounded timeout,
#     infrastructure error, no success-shaped stdout ------------------------
test_idle_without_answer_times_out() {
  note "scenario: agent reports idle/done repeatedly but never writes an answer"
  new_scenario
  printf 'idle\n' >"$SCEN/statuses"
  MOCK_MAX_WAIT=0.5 run_worker "$SCEN" "$WT"
  assert_eq "exits non-zero (infrastructure error, not success)" "$RC" "1"
  assert_eq "stdout is empty -- no terminal-status-line stand-in for the answer" "$OUT" ""
  assert_contains "stderr explains the timeout" "$ERR" "timed out"
  assert_contains "close still runs (cleanup happens exactly once, on the way out)" "$(cat "$SCEN/close.log" 2>/dev/null || true)" "close"
  rm -rf "$SCEN"
}

# --- scenario 5: the agent disappears mid-run -------------------------------
test_agent_lost_mid_run() {
  note "scenario: agent process/pane disappears before writing an answer"
  new_scenario
  printf 'idle\nFAIL\n' >"$SCEN/statuses"
  run_worker "$SCEN" "$WT"
  assert_eq "exits non-zero" "$RC" "1"
  assert_eq "stdout is empty -- transcript tail is never used as a stand-in answer" "$OUT" ""
  assert_contains "stderr says the agent was lost" "$ERR" "was lost"
  rm -rf "$SCEN"
}

# --- scenario 6: answer written non-atomically must not be read mid-write --
test_answer_completes_atomically() {
  note "scenario: answer file is written in two chunks; must not be read half-done"
  new_scenario
  printf 'idle\nidle\nidle\nidle\nidle\nidle\n' >"$SCEN/statuses"
  # The writer must only start once herdr-agent-worker.sh's own `rm -f "$ans"`
  # has already run (near the top of the script, well before its first
  # `agent get` poll) -- otherwise that rm could race the writer's first
  # write and silently eat the "PART1-" chunk. Kick it off from the first
  # `agent get` call instead of before run_worker, so ordering is guaranteed.
  cat >"$SCEN/on_call_1" <<EOF
( printf 'KANBAN_ANSWER_ID: test-card|wt|worker|attempt-1\nPART1-' >"$WT/.kanban-answer.md"
  sleep 0.25
  printf 'PART2-DONE\n' >>"$WT/.kanban-answer.md" ) &
EOF
  MOCK_MAX_WAIT=3 run_worker "$SCEN" "$WT"
  assert_eq "completes successfully once stable" "$RC" "0"
  assert_contains "full content present" "$OUT" "PART1-PART2-DONE"
  assert_not_contains "never emitted a truncated mid-write read as the answer" "$OUT" $'PART1-\n'
  rm -rf "$SCEN"
}

# --- role/backend matrix: the completion gating and timeout paths must hold
#     for worker, reviewer, and resolver, on both Claude and Codex -----------
test_role_backend_matrix() {
  local role backend wt
  for role in worker reviewer resolver; do
    for backend in claude codex; do
      note "scenario: role=$role backend=$backend transient idle then late answer"
      new_scenario
      printf 'idle\nworking\nidle\nidle\n' >"$SCEN/statuses"
      cat >"$SCEN/on_call_3" <<EOF
printf 'KANBAN_ANSWER_ID: test-card|wt|$role|attempt-1\n{"score": 90, "feedback": "ok"}\n' > "$WT/.kanban-answer.md"
EOF
      run_worker "$SCEN" "$WT" "$role" "$backend"
      assert_eq "[$role/$backend] settles only once the answer lands" "$RC" "0"
      assert_contains "[$role/$backend] stdout carries the real answer" "$OUT" '"score": 90'
      rm -rf "$SCEN"

      note "scenario: role=$role backend=$backend no-answer timeout"
      new_scenario
      printf 'idle\n' >"$SCEN/statuses"
      MOCK_MAX_WAIT=0.5 run_worker "$SCEN" "$WT" "$role" "$backend"
      assert_eq "[$role/$backend] times out as an infra error" "$RC" "1"
      assert_eq "[$role/$backend] stdout stays empty on timeout" "$OUT" ""
      rm -rf "$SCEN"
    done
  done
}

# --- wrong-card/stale answer is rejected even when stable -----------------
test_wrong_identity_is_rejected() {
  note "scenario: stable answer belongs to another card/attempt"
  new_scenario
  printf 'idle\nidle\n' >"$SCEN/statuses"
  cat >"$SCEN/on_call_1" <<EOF
printf 'KANBAN_ANSWER_ID: other-card|wt|worker|old-attempt\nSTALE CONTENT\n' > "$WT/.kanban-answer.md"
EOF
  run_worker "$SCEN" "$WT"
  assert_eq "rejects stale answer with non-zero status" "$RC" "1"
  assert_contains "reports identity mismatch" "$ERR" "answer identity mismatch"
  assert_not_contains "does not emit stale answer body" "$OUT" "STALE CONTENT"
  rm -rf "$SCEN"
}

test_diagnosis_timeout_is_scope_block_not_infra_retry() {
  note "scenario: diagnosis reaches its hard timebox without an answer"
  new_scenario
  printf 'idle\n' >"$SCEN/statuses"
  MOCK_CARD_KIND=diagnose MOCK_MAX_WAIT=0.5 run_worker "$SCEN" "$WT"
  assert_eq "diagnosis timeout exits non-zero" "$RC" "1"
  assert_contains "uses scope/timebox category" "$ERR" "KANBAN_INFRA_ERROR: scope_timebox"
  rm -rf "$SCEN"
}

test_transient_idle_then_reworking_then_answer
test_blocked_false_positive_running_shell
test_blocked_true_permission_prompt
test_idle_without_answer_times_out
test_agent_lost_mid_run
test_answer_completes_atomically
test_role_backend_matrix
test_wrong_identity_is_rejected
test_diagnosis_timeout_is_scope_block_not_infra_retry

note ""
note "passed: $pass_count  failed: $fail_count"
[[ $fail_count -eq 0 ]]
