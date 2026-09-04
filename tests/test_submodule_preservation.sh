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

# fixtures below use file:// submodule URLs (local sibling dirs); git blocks
# that transport by default, so allow it for this test process only -- real
# projects submodule over https/ssh and never touch this.
export GIT_ALLOW_PROTOCOL=file

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

# --- fixture: a main repo with a submodule added, but *not* initialized
# anywhere -- mirrors the state `git worktree add` leaves a fresh card
# worktree in, before init_submodules runs.
build_uninitialized_fixture() { # build_uninitialized_fixture <scenario-dir> -> echoes "root_repo worktree"
  local dir=$1 sub main wt
  sub=$dir/sub
  main=$dir/main
  git init -q "$sub"
  git -C "$sub" commit -q --allow-empty -m "sub init"
  git init -q "$main"
  git -C "$main" commit -q --allow-empty -m "main init"
  git -C "$main" -c protocol.file.allow=always submodule add -q "$sub" subdir
  git -C "$main" commit -q -m "add submodule"
  rm -rf "$main/.git/modules/subdir"
  wt=$dir/wt1
  git -C "$main" -c protocol.file.allow=always worktree add -q -b kanban/wt1 "$wt" master
  printf '%s %s\n' "$main" "$wt"
}

# --- scenario 6: init_submodules, run right after `git worktree add` (the
# real call sites in process_card_wt / process_resolve_wt), populates the
# submodule directory in a project that has .gitmodules.
test_init_submodules_populates_after_worktree_add() {
  local dir=$WORKDIR/s7 root wt log
  mkdir -p "$dir"
  read -r root wt < <(build_uninitialized_fixture "$dir")
  log=$dir/init.log
  probe test -z "$(ls -A "$wt/subdir" 2>/dev/null)"
  assert_true "submodule dir is empty right after worktree add (baseline)" "$STATUS"
  probe init_submodules "$wt" "$log"
  assert_true "init_submodules succeeds for a project with .gitmodules" "$STATUS"
  probe test -n "$(ls -A "$wt/subdir" 2>/dev/null)"
  assert_true "init_submodules populates the submodule directory" "$STATUS"
}

# --- scenario 7: a project without .gitmodules pays nothing -- no git
# process spawned, no log written, early return.
test_init_submodules_noop_without_gitmodules() {
  local dir=$WORKDIR/s8 root wt log
  mkdir -p "$dir"
  root=$dir/main
  git init -q "$root"
  git -C "$root" commit -q --allow-empty -m init
  wt=$dir/wt1
  git -C "$root" worktree add -q -b kanban/wt1 "$wt" master
  log=$dir/init.log
  probe init_submodules "$wt" "$log"
  assert_true "init_submodules no-ops without .gitmodules" "$STATUS"
  probe test ! -e "$log"
  assert_true "init_submodules writes nothing when it no-ops" "$STATUS"
}

# --- scenario 8: init failure is surfaced (non-zero exit) and captured in
# the log, not swallowed -- this is what call sites route into an
# infrastructure fail_card.
test_init_submodules_reports_and_logs_failure() {
  local dir=$WORKDIR/s9 root wt log
  mkdir -p "$dir"
  read -r root wt < <(build_uninitialized_fixture "$dir")
  log=$dir/init.log
  # break the recorded submodule URL so `submodule update --init` fails.
  # `submodule add` wrote submodule.subdir.url into the superproject's
  # .git/config too (shared with this worktree) -- that takes precedence
  # over .gitmodules, so both must be corrupted for the fetch to actually fail.
  git -C "$wt" config -f "$wt/.gitmodules" submodule.subdir.url "$dir/does-not-exist"
  git -C "$wt" config submodule.subdir.url "$dir/does-not-exist"
  probe init_submodules "$wt" "$log"
  assert_false "init_submodules reports failure when the submodule can't be fetched" "$STATUS"
  probe test -s "$log"
  assert_true "init_submodules logs the failure instead of swallowing it" "$STATUS"
}

# --- scenario 9: the actual incident this card fixes -- a commit preserved
# into the shared store (never pushed to the submodule's own origin) must be
# checkoutable in a *new* worktree created after the original one is gone.
# Before this card's fix, init_submodules only ran a plain `submodule update
# --init`, which fetches from origin alone and never sees the shared store,
# so the checkout failed with "unable to read tree" even though the object
# was safe in COMMON/modules/subdir.
test_checkout_in_new_worktree_after_recreation() {
  local dir=$WORKDIR/s10 root wt gitlink wt2 log
  mkdir -p "$dir"
  read -r root wt gitlink < <(build_fixture "$dir")
  ROOT=$root
  # preserve, then destroy the worktree that holds the only copy of the
  # commit -- the submodule's own origin never received it.
  kanban_remove_worktree "$wt"
  probe test ! -d "$wt"
  assert_true "original worktree is gone" "$STATUS"

  # a later card gets a brand new worktree on the same branch (the gitlink
  # commit that points at $gitlink is already on kanban/wt1).
  wt2=$dir/wt2
  git -C "$root" worktree add -q "$wt2" kanban/wt1
  log=$dir/init2.log
  probe init_submodules "$wt2" "$log"
  assert_true "init_submodules succeeds in the new worktree using the preserved commit" "$STATUS"
  probe bash -c "[[ \$(git -C '$wt2/subdir' rev-parse HEAD 2>/dev/null) == '$gitlink' ]]"
  assert_true "new worktree's submodule is checked out at the preserved gitlink sha" "$STATUS"
}

test_preserve_survives_worktree_removal
test_without_preserve_object_is_lost
test_kanban_remove_worktree_preserves_automatically
test_verify_gate_reflects_preservation_state
test_verify_gate_passes_when_preserved
test_noop_without_submodules
test_init_submodules_populates_after_worktree_add
test_init_submodules_noop_without_gitmodules
test_init_submodules_reports_and_logs_failure
test_checkout_in_new_worktree_after_recreation

note ""
note "pass=$pass_count fail=$fail_count"
[[ $fail_count -eq 0 ]]
