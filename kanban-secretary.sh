#!/usr/bin/env bash
# kanban-secretary.sh - bootstrap a MornKanban secretary and dispatch visible
# Herdr workers without silently falling back to headless agent processes.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
KANBAN_BIN=${KANBAN_BIN:-$REPO/kanban.sh}
SECRETARY_NAME=${KANBAN_HERDR_SECRETARY:-secretary}

die() { echo "kanban-secretary: $*" >&2; exit 1; }

require_herdr() {
  [[ ${HERDR_ENV:-} == 1 ]] || die "not running inside Herdr (HERDR_ENV != 1); refusing a hidden headless fallback"
  [[ -n ${HERDR_PANE_ID:-} ]] || die "HERDR_PANE_ID is missing; refusing a hidden headless fallback"
  command -v herdr >/dev/null 2>&1 || die "herdr command was not found"
  herdr pane layout --current >/dev/null || die "the current Herdr pane could not be verified"
}

project_root() {
  local start=${1:-$PWD} d
  d=$(cd "$start" && pwd)
  while [[ $d != / ]]; do
    if [[ -d $d/.kanban ]]; then echo "$d"; return 0; fi
    d=$(dirname "$d")
  done
  return 1
}

json_value() {
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"
}

split_direction() {
  herdr pane layout --current | python3 -c '
import json, os, sys
layout = json.load(sys.stdin)["result"]["layout"]
pane_id = os.environ["HERDR_PANE_ID"]
for pane in layout["panes"]:
    if pane["pane_id"] == pane_id:
        rect = pane["rect"]
        print("right" if rect["width"] > 2 * rect["height"] else "down")
        break
else:
    raise SystemExit("current pane was absent from Herdr layout")
'
}

shell_quote() {
  python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$1"
}

bootstrap() {
  local target=${1:-$PWD} root git_root
  require_herdr
  target=$(cd "$target" && pwd)
  if root=$(project_root "$target"); then
    target=$root
  elif git_root=$(git -C "$target" rev-parse --show-toplevel 2>/dev/null); then
    target=$git_root
  fi
  # `kanban init` is idempotent and never overwrites an existing KANBAN.md.
  (cd "$target" && "$KANBAN_BIN" init)
  root=$(project_root "$target") || die "kanban init did not create .kanban"
  [[ -f $root/.kanban/KANBAN.md ]] || die "$root/.kanban/KANBAN.md is missing"

  # Notifications address the secretary by this stable Herdr agent name.
  herdr agent rename "$HERDR_PANE_ID" "$SECRETARY_NAME" >/dev/null ||
    die "could not register this agent as '$SECRETARY_NAME'"

  # Record this pane as the project's active secretary. Project/pane scoped;
  # a re-bootstrap in a new pane silently supersedes a stale marker left by a
  # dead pane. Read by guard/claude_secretary_guard.py to deny in-process
  # subagent delegation from inside this exact pane only.
  python3 -c '
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "guard"))
import secretary_marker as marker
marker.write_marker(sys.argv[2], os.environ["HERDR_PANE_ID"], sys.argv[3])
' "$REPO" "$root" "$SECRETARY_NAME" || die "could not write the active-secretary marker"

  echo "secretary ready: project=$root agent=$SECRETARY_NAME execution=visible-herdr guard=$(guard_status_line)"
}

guard_status_line() {
  if [[ -f $REPO/guard/claude_secretary_guard.py ]]; then
    echo "claude=$( [[ -f ~/.claude/settings.json ]] && grep -q claude_secretary_guard.py ~/.claude/settings.json 2>/dev/null && echo enforced || echo not-installed ),codex=prompt-only"
  else
    echo "unavailable"
  fi
}

end() {
  local target=${1:-$PWD} root
  root=$(project_root "$target") || die "no .kanban directory found"
  python3 -c '
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "guard"))
import secretary_marker as marker
marker.clear_marker(sys.argv[2])
' "$REPO" "$root"
  echo "secretary marker cleared: project=$root"
}

dispatch() {
  local once=false target=$PWD root lock direction pane command
  if [[ ${1:-} == --once ]]; then once=true; shift; fi
  if [[ $# -gt 0 ]]; then target=$1; shift; fi
  [[ $# -eq 0 ]] || die "usage: $0 dispatch [--once] [project-dir]"

  require_herdr
  root=$(project_root "$target") || die "no .kanban directory found (run bootstrap first)"
  lock=$root/.kanban/.lock
  if [[ -f $lock ]] && kill -0 "$(cat "$lock")" 2>/dev/null; then
    echo "dispatcher already running: pid=$(cat "$lock")"
    return 0
  fi

  direction=$(split_direction)
  pane=$(herdr pane split --current --direction "$direction" --cwd "$root" --no-focus |
    json_value 'd["result"]["pane"]["pane_id"]')
  herdr pane rename "$pane" "kanban dispatcher" >/dev/null 2>&1 || true

  command="env KANBAN_WORKER_CMD=$(shell_quote "$REPO/herdr-agent-worker.sh")"
  command="$command KANBAN_REVIEW_CMD=$(shell_quote "env KANBAN_HERDR_ROLE=reviewer $REPO/herdr-agent-worker.sh")"
  command="$command KANBAN_RESOLVE_CMD=$(shell_quote "env KANBAN_HERDR_ROLE=resolver $REPO/herdr-agent-worker.sh")"
  command="$command KANBAN_NOTIFY_CMD=$(shell_quote "$REPO/herdr-notify-secretary.sh")"
  command="$command KANBAN_HERDR_SECRETARY=$(shell_quote "$SECRETARY_NAME")"
  command="$command $(shell_quote "$KANBAN_BIN") run"
  if $once; then command="$command --once"; fi
  command="$command; exit"

  if ! herdr pane run "$pane" "$command" >/dev/null; then
    herdr pane close "$pane" >/dev/null 2>&1 || true
    die "failed to start the dispatcher pane"
  fi
  echo "dispatcher started: pane=$pane execution=visible-herdr"
}

case ${1:-} in
  bootstrap) shift; bootstrap "$@" ;;
  dispatch) shift; dispatch "$@" ;;
  end) shift; end "$@" ;;
  *) die "usage: $0 <bootstrap [project-dir] | dispatch [--once] [project-dir] | end [project-dir]>" ;;
esac
