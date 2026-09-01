#!/usr/bin/env bash
# test_review_prompt_injection.sh - review_prompt_for_card / review_prompt_for_resolve
# must never re-parse worker report / card text as shell.
#
# Bug: both functions built the reviewer prompt with an UNQUOTED heredoc
# (`cat <<EOF ... $report ... EOF`), so bash performed command substitution
# on whatever the worker/agent had written into its report. A report
# containing "$(", a backtick, or an unbalanced paren -- routine in long
# Japanese feedback describing code -- crashed kanban.sh with a
# content-dependent "syntax error near unexpected token" instead of
# producing a reviewer prompt, which is exactly what turned valid worker
# output into wrapper_error/unparseable_output review-infra failures.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

fail_count=0
pass_count=0
note() { printf '%s\n' "$*" >&2; }
ok() { pass_count=$((pass_count + 1)); note "  ok - $1"; }
bad() { fail_count=$((fail_count + 1)); note "  FAIL - $1"; }
assert_contains() { # assert_contains <label> <haystack> <needle>
  if [[ $2 == *"$3"* ]]; then ok "$1"; else bad "$1 (missing [$3] in [$2])"; fi
}

# kanban.sh runs a `case ${1:-} in ... esac` CLI dispatch as the last thing
# in the file, so it cannot be `source`d directly for its functions. Load
# only the function/variable definitions that precede that dispatch.
FUNCS=$(mktemp)
trap 'rm -f "$FUNCS"' EXIT
awk '/^case \$\{1:-\} in$/{exit} {print}' "$REPO/kanban.sh" |
  sed "s#^SELF_DIR=\$(resolve_self_dir \"\$0\")\$#SELF_DIR=$REPO#" >"$FUNCS"
grep -q "^SELF_DIR=$REPO\$" "$FUNCS" || { note "FAIL - could not pin SELF_DIR while extracting kanban.sh functions"; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"; rm -f "$FUNCS"' EXIT
KB="$WORK/.kanban"
mkdir -p "$KB/backlog" "$KB/reports"

card="$KB/backlog/20260101-000000-1-t.md"
cat >"$card" <<'CARDEOF'
---
id: 20260101-000000-1
title: t
attempts: 0
---
## Task

do the thing

## History
CARDEOF

# Report content crafted from the exact hazards named in the bug report:
# unbalanced $(, a backtick, bare parens, "done", newlines, Japanese text.
report_path="$KB/reports/20260101-000000-1-r1.md"
cat >"$report_path" <<'REPORTEOF'
実行結果 (確認済み): $(rm -rf /tmp/should-not-run
バッククォートも含む `echo pwned` テスト。
while true; do echo x; done
括弧だけの行 (
長い日本語のフィードバックです。改行や引用符"やparen)を含みます。
REPORTEOF

(
  set -euo pipefail
  # shellcheck source=/dev/null
  source "$FUNCS"
  KB="$KB"
  prompt=$(review_prompt_for_card "$card")
  printf '%s' "$prompt" >"$WORK/prompt_for_card.out"

  prompt2=$(review_prompt_for_resolve "$card" "kanban/20260101-000000-1" "main")
  printf '%s' "$prompt2" >"$WORK/prompt_for_resolve.out"
)
status=$?

if [[ $status -ne 0 ]]; then
  bad "review_prompt_for_card / review_prompt_for_resolve must not raise a shell error on hostile report content (exit $status)"
else
  ok "review_prompt_for_card / review_prompt_for_resolve ran without a shell syntax error"
  out=$(cat "$WORK/prompt_for_card.out")
  assert_contains "worker report text is passed through verbatim" "$out" 'rm -rf /tmp/should-not-run'
  assert_contains "backtick text is passed through verbatim, not executed" "$out" 'echo pwned'
  assert_contains "bare parens are passed through verbatim" "$out" '括弧だけの行 ('
  assert_contains "a stray done is passed through verbatim" "$out" 'while true; do echo x; done'
  assert_contains "reviewer JSON contract still present" "$out" 'Output ONLY a JSON object'

  out2=$(cat "$WORK/prompt_for_resolve.out")
  assert_contains "resolve prompt still contains the card task" "$out2" 'do the thing'
  assert_contains "resolve prompt still contains the reviewer JSON contract" "$out2" 'Output ONLY a JSON object'
fi

# Sanity marker: this file was never executed as a shell command / it did
# not leave a trace file behind.
[[ ! -e /tmp/should-not-run ]] && ok "hostile \$(...) in report content was never executed" ||
  bad "hostile \$(...) in report content WAS executed -- injection succeeded"

note ""
note "PASS: $pass_count  FAIL: $fail_count"
[[ $fail_count -eq 0 ]]
