#!/usr/bin/env bash
# Shared Git project and board resolution for shell entry points.

kanban_project_root() { # kanban_project_root <start-dir>
  local start=${1:-$PWD} root
  [[ -d $start ]] || return 1
  root=$(git -C "$start" worktree list --porcelain 2>/dev/null |
    awk '/^worktree /{sub(/^worktree /, ""); print; exit}') || return 1
  [[ -n $root && -d $root ]] || return 1
  (cd "$root" && pwd -P)
}

kanban_board_dir() { # kanban_board_dir <start-dir>
  local start=${1:-$PWD} common
  common=$(git -C "$start" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 1
  [[ -d $common ]] || return 1
  printf '%s/kanban\n' "${common%/}"
}
