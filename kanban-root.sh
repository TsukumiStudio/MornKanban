#!/usr/bin/env bash
# kanban-root.sh - shared project-root resolution, sourced by
# kanban-secretary.sh and herdr-notify-secretary.sh so every entry point
# agrees on the same project identity (used to derive the per-project
# secretary name; see registry/secretary.py).
#
# A .kanban/wt/<id> worktree checkout carries its own tracked .kanban/
# subtree (todo/doing/review/... are committed, only wt/ itself is
# gitignored), so a naive upward search for a directory containing
# .kanban would stop at the worktree root and misidentify it as its own
# project. Strip any "/.kanban/wt/<id>/..." suffix first so we always land
# on the outer project root, whether invoked from the project root itself,
# a subdirectory, or inside one of its own card worktrees.

kanban_project_root() { # kanban_project_root <start-dir> -> prints realpath root, or fails
  local start=${1:-$PWD} d
  d=$(cd "$start" && pwd) || return 1
  case $d in
    */.kanban/wt/*) d=${d%%/.kanban/wt/*} ;;
  esac
  while [[ $d != / ]]; do
    if [[ -d $d/.kanban ]]; then echo "$d"; return 0; fi
    d=$(dirname "$d")
  done
  return 1
}
