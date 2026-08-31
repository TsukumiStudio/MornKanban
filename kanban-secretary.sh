#!/usr/bin/env bash
# kanban-secretary.sh - bootstrap a MornKanban secretary and dispatch visible
# Herdr workers without silently falling back to headless agent processes.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
KANBAN_BIN=${KANBAN_BIN:-$REPO/kanban.sh}
REGISTRY_CLI=$REPO/registry/cli.py
# shellcheck source=kanban-root.sh
source "$REPO/kanban-root.sh"

die() { echo "kanban-secretary: $*" >&2; exit 1; }

require_herdr() {
  [[ ${HERDR_ENV:-} == 1 ]] || die "not running inside Herdr (HERDR_ENV != 1); refusing a hidden headless fallback"
  [[ -n ${HERDR_PANE_ID:-} ]] || die "HERDR_PANE_ID is missing; refusing a hidden headless fallback"
  command -v herdr >/dev/null 2>&1 || die "herdr command was not found"
  herdr pane layout --current >/dev/null || die "the current Herdr pane could not be verified"
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

# resolve_secretary_name <root> -> sets SECRETARY_NAME / SECRETARY_SOURCE.
# Priority (enforced by registry/secretary.py, shared with dispatch and the
# herdr-notify-secretary.sh fallback): environment (KANBAN_HERDR_SECRETARY)
# > .kanban/KANBAN.md `secretary_agent:` > generated `secretary-<slug>`
# default. An invalid explicit override fails loudly here instead of
# silently falling back to a different name.
resolve_secretary_name() {
  local root=$1 out
  if ! out=$(python3 "$REGISTRY_CLI" secretary resolve "$root" 2>&1); then
    die "could not resolve a secretary agent name for $root: $out"
  fi
  SECRETARY_NAME=$(echo "$out" | json_value 'd["name"]')
  SECRETARY_SOURCE=$(echo "$out" | json_value 'd["source"]')
}

bootstrap() {
  local target=${1:-$PWD} root git_root
  require_herdr
  target=$(cd "$target" && pwd)
  if root=$(kanban_project_root "$target"); then
    target=$root
  elif git_root=$(git -C "$target" rev-parse --show-toplevel 2>/dev/null); then
    target=$git_root
  fi
  # `kanban init` is idempotent and never overwrites an existing KANBAN.md.
  (cd "$target" && "$KANBAN_BIN" init)
  root=$(kanban_project_root "$target") || die "kanban init did not create .kanban"
  [[ -f $root/.kanban/KANBAN.md ]] || die "$root/.kanban/KANBAN.md is missing"

  resolve_secretary_name "$root"

  # Notifications address the secretary by this stable, project-specific
  # Herdr agent name. A rename failure here means the name is already in
  # use (most likely by another project's running secretary) - this agent
  # is left unrenamed and no other project's agent is touched or taken over.
  herdr agent rename "$HERDR_PANE_ID" "$SECRETARY_NAME" >/dev/null || die "\
could not register this agent as '$SECRETARY_NAME' for project '$root' (name source: $SECRETARY_SOURCE).
If another project's secretary is already using this name, set a distinct one and bootstrap again:
  - one-off: KANBAN_HERDR_SECRETARY=<name> $0 bootstrap $root
  - persistent: add 'secretary_agent: <name>' to $root/.kanban/KANBAN.md frontmatter
This agent was NOT renamed; no other project's running agent was touched."

  echo "secretary ready: project=$root secretary=$SECRETARY_NAME (name source: $SECRETARY_SOURCE) execution=visible-herdr"
}

dispatch() {
  local once=false target=$PWD root lock direction pane command
  if [[ ${1:-} == --once ]]; then once=true; shift; fi
  if [[ $# -gt 0 ]]; then target=$1; shift; fi
  [[ $# -eq 0 ]] || die "usage: $0 dispatch [--once] [project-dir]"

  require_herdr
  root=$(kanban_project_root "$target") || die "no .kanban directory found (run bootstrap first)"
  lock=$root/.kanban/.lock
  if [[ -f $lock ]] && kill -0 "$(cat "$lock")" 2>/dev/null; then
    echo "dispatcher already running: pid=$(cat "$lock")"
    return 0
  fi

  resolve_secretary_name "$root"
  direction=$(split_direction)
  pane=$(herdr pane split --current --direction "$direction" --cwd "$root" --no-focus |
    json_value 'd["result"]["pane"]["pane_id"]')
  herdr pane rename "$pane" "kanban dispatcher" >/dev/null 2>&1 || true

  command="env KANBAN_WORKER_CMD=$(shell_quote "$REPO/herdr-agent-worker.sh")"
  command="$command KANBAN_REVIEW_CMD=$(shell_quote "env KANBAN_HERDR_ROLE=reviewer $REPO/herdr-agent-worker.sh")"
  command="$command KANBAN_NOTIFY_CMD=$(shell_quote "$REPO/herdr-notify-secretary.sh")"
  command="$command KANBAN_HERDR_SECRETARY=$(shell_quote "$SECRETARY_NAME")"
  command="$command $(shell_quote "$KANBAN_BIN") run"
  if $once; then command="$command --once"; fi
  command="$command; exit"

  if ! herdr pane run "$pane" "$command" >/dev/null; then
    herdr pane close "$pane" >/dev/null 2>&1 || true
    die "failed to start the dispatcher pane"
  fi
  echo "dispatcher started: pane=$pane secretary=$SECRETARY_NAME execution=visible-herdr"
}

case ${1:-} in
  bootstrap) shift; bootstrap "$@" ;;
  dispatch) shift; dispatch "$@" ;;
  *) die "usage: $0 <bootstrap [project-dir] | dispatch [--once] [project-dir]>" ;;
esac
