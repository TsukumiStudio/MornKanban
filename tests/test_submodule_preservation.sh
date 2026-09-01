#!/usr/bin/env bash
# test_submodule_preservation.sh - regression coverage for the worktree
# submodule-loss hole: a worker that commits inside a submodule initialized
# only within its card worktree gets its own private gitdir under
# .git/worktrees/<wt>/modules/<name>. `git worktree remove --force` deletes
# that gitdir -- and every submodule object in it -- leaving the parent's
# gitlink pointing at a commit that no longer exists anywhere.
#
# This drives the real preserve_submodule_objects / verify_submodule_gitlinks
# / kanban_remove_worktree functions from kanban.sh (unmodified, sourced
# directly) against real git repositories and worktrees. No mocks.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
KANBAN_SH="$REPO/kanban.sh"

fail_count=0
pass_count=0
note() { printf '%s\n' "$*" >&2; }
ok() { pass_count=$((pass_count + 1)); note "  ok - $1"; }
bad() { fail_count=$((fail_count + 1)); note "  FAIL - $1"; }
assert_true() { # assert_true <label> <exit-status>
  if [[ $2 -eq 0 ]]; then ok "$1"; else bad "$1 (exit $2)"; fi
}
assert_false() { # assert_false <label> <exit-status>
  if [[ $2 -ne 0 ]]; then ok "$1"; else bad "$1 (unexpectedly succeeded)"; fi
}
# probe <cmd...> -> runs cmd with errexit suspended, leaves status in $STATUS
probe() { set +e; "$@"; STATUS=$?; set -e; }

# --- load kanban.sh's functions without running its trailing `case` dispatch.
# SELF_DIR is pinned to $REPO directly (kanban.sh normally derives it from
# $0, which here would resolve to this test file's directory instead).
KANBAN_FUNCS=$(mktemp)
sed -e '/^SELF_DIR=\$(resolve_self_dir "\$0")$/d' \
    -e '/^case \${1:-} in$/,$d' \
    "$KANBAN_SH" > "$KANBAN_FUNCS"
SELF_DIR=$REPO
# shellcheck disable=SC1090
source "$KANBAN_FUNCS"
rm -f "$KANBAN_FUNCS"

WORKDIR=$(mktemp -d)
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

# --- fixture: a main repo with a submodule added, plus a linked worktree
# that initializes the submodule *itself* (never touched by the main
# checkout), advances it, and records the gitlink -- the exact shape of the
# real incident this card fixes.
build_fixture() { # build_fixture <scenario-dir> -> echoes "root_repo worktree gitlink_sha"
  local dir=$1 sub main wt
  sub=$dir/sub
  main=$dir/main
  git init -q "$sub"
  git -C "$sub" commit -q --allow-empty -m "sub init"
  git init -q "$main"
  git -C "$main" commit -q --allow-empty -m "main init"
  git -C "$main" -c protocol.file.allow=always submodule add -q "$sub" subdir
  git -C "$main" commit -q -m "add submodule"
  # simulate a fresh checkout that never locally initialized the submodule
  rm -rf "$main/.git/modules/subdir"
  wt=$dir/wt1
  git -C "$main" worktree add -q -b kanban/wt1 "$wt" master
  git -C "$wt" -c protocol.file.allow=always submodule update -q --init
  git -C "$wt/subdir" commit -q --allow-empty -m "worker commit inside submodule"
  local gitlink
  gitlink=$(git -C "$wt/subdir" rev-parse HEAD)
  git -C "$wt" add subdir
  git -C "$wt" commit -q -m "advance gitlink"
  printf '%s %s %s\n' "$main" "$wt" "$gitlink"
}

# --- scenario 1: preserve_submodule_objects migrates the objects, and they
# stay reachable from the parent's shared store after the worktree is gone.
test_preserve_survives_worktree_removal() {
  local dir=$WORKDIR/s1 root wt gitlink common
  mkdir -p "$dir"
  read -r root wt gitlink < <(build_fixture "$dir")
  ROOT=$root
  preserve_submodule_objects "$wt"
  git -C "$root" worktree remove --force "$wt"
  git -C "$root" worktree prune
  common=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)
  probe git -C "$common/modules/subdir" cat-file -e "${gitlink}^{commit}"
  assert_true "preserve_submodule_objects keeps the gitlink commit reachable after worktree remove" "$STATUS"
}

# --- scenario 2: without preservation (the bug as reported), the object is
# gone once the worktree is force-removed -- proving the check in scenario 3
# is not a tautology.
test_without_preserve_object_is_lost() {
  local dir=$WORKDIR/s2 root wt gitlink common
  mkdir -p "$dir"
  read -r root wt gitlink < <(build_fixture "$dir")
  ROOT=$root
  # no preserve_submodule_objects call: this is the pre-fix behavior
  git -C "$root" worktree remove --force "$wt"
  git -C "$root" worktree prune
  common=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)
  if [[ -d $common/modules/subdir ]]; then
    probe git -C "$common/modules/subdir" cat-file -e "${gitlink}^{commit}"
  else
    STATUS=1
  fi
  assert_false "without preservation the gitlink commit is unreachable (reproduces the incident)" "$STATUS"
}

# --- scenario 3: kanban_remove_worktree (the wrapper every removal call
# site now goes through) preserves automatically -- no caller has to
# remember to call preserve_submodule_objects itself.
test_kanban_remove_worktree_preserves_automatically() {
  local dir=$WORKDIR/s3 root wt gitlink common
  mkdir -p "$dir"
  read -r root wt gitlink < <(build_fixture "$dir")
  ROOT=$root
  KB=$dir/kb  # unused by kanban_remove_worktree, but keep globals sane
  kanban_remove_worktree "$wt"
  common=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)
  probe git -C "$common/modules/subdir" cat-file -e "${gitlink}^{commit}"
  assert_true "kanban_remove_worktree preserves submodule objects before removing" "$STATUS"
  probe test ! -d "$wt"
  assert_true "kanban_remove_worktree actually removes the worktree" "$STATUS"
}

# --- scenario 4: verify_submodule_gitlinks is the merge-completion gate --
# it must refuse (with a reason) when preservation was skipped, i.e.
# disabling the guard makes this test fail instead of silently letting a
# card with a dangling gitlink reach `done`.
test_verify_gate_reflects_preservation_state() {
  local dir=$WORKDIR/s4 root wt gitlink
  mkdir -p "$dir"
  read -r root wt gitlink < <(build_fixture "$dir")
  ROOT=$root

  # preservation skipped (guard disabled) -> verify must refuse
  git -C "$root" worktree remove --force "$wt"
  git -C "$root" worktree prune
  probe verify_submodule_gitlinks kanban/wt1 master
  assert_false "verify_submodule_gitlinks fails closed when preservation was skipped" "$STATUS"
  probe test -n "$VERIFY_SUBMODULE_REASON"
  assert_true "verify_submodule_gitlinks records a reason for history" "$STATUS"
}

test_verify_gate_passes_when_preserved() {
  local dir=$WORKDIR/s5 root wt gitlink
  mkdir -p "$dir"
  read -r root wt gitlink < <(build_fixture "$dir")
  ROOT=$root
  preserve_submodule_objects "$wt"
  git -C "$root" worktree remove --force "$wt"
  git -C "$root" worktree prune
  probe verify_submodule_gitlinks kanban/wt1 master
  assert_true "verify_submodule_gitlinks passes once objects were preserved" "$STATUS"
}

# --- scenario 5: no-submodule projects pay effectively nothing -- both
# functions must no-op cleanly (used on every card in a plain project).
test_noop_without_submodules() {
  local dir=$WORKDIR/s6 root wt
  mkdir -p "$dir"
  root=$dir/main
  git init -q "$root"
  git -C "$root" commit -q --allow-empty -m init
  wt=$dir/wt1
  git -C "$root" worktree add -q -b kanban/wt1 "$wt" master
  ROOT=$root
  probe preserve_submodule_objects "$wt"
  assert_true "preserve_submodule_objects no-ops without submodules" "$STATUS"
  probe verify_submodule_gitlinks kanban/wt1 master
  assert_true "verify_submodule_gitlinks no-ops without submodules" "$STATUS"
}

test_preserve_survives_worktree_removal
test_without_preserve_object_is_lost
test_kanban_remove_worktree_preserves_automatically
test_verify_gate_reflects_preservation_state
test_verify_gate_passes_when_preserved
test_noop_without_submodules

note ""
note "pass=$pass_count fail=$fail_count"
[[ $fail_count -eq 0 ]]
