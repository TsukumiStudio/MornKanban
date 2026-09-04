#!/usr/bin/env bash
# test_submodule_publish_card.sh - regression coverage for the "submodule
# commit merged but never published" hole: once a card branch's gitlink
# change is merged into base, the submodule commit exists only in this
# machine's local shared object store. If the parent repo is pushed before
# that submodule commit reaches its own remote, any other clone/CI/machine
# hits "upload-pack: not our ref" the moment it tries to resolve the gitlink.
#
# This drives the real enqueue_submodule_publish_cards / submodule_gitlink_diff
# / submodule_publish_card_pending / worker_prompt_for_card functions from
# kanban.sh (unmodified, sourced directly) against real git repositories and
# a real kanban board. No mocks.
set -euo pipefail

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
assert_eq() { # assert_eq <label> <expected> <actual>
  if [[ $2 == "$3" ]]; then ok "$1"; else bad "$1 (expected '$2', got '$3')"; fi
}
probe() { set +e; "$@"; STATUS=$?; set -e; }

# --- load kanban.sh's functions without running its trailing `case` dispatch
# (same technique as test_submodule_preservation.sh).
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

# --- fixture: a main repo with a submodule pinned to sha1 on master, and a
# topic branch that advances the gitlink to sha2 (the shape of a merged card
# branch that changed a submodule commit). Board is initialized via cmd_init.
build_fixture() { # build_fixture <scenario-dir> -> echoes "root sha1 sha2"
  local dir=$1 sub main sha1 sha2
  sub=$dir/sub
  main=$dir/main
  git init -q "$sub"
  git -C "$sub" commit -q --allow-empty -m "sub c1"
  sha1=$(git -C "$sub" rev-parse HEAD)
  git -C "$sub" commit -q --allow-empty -m "sub c2"
  sha2=$(git -C "$sub" rev-parse HEAD)
  git init -q "$main"
  git -C "$main" commit -q --allow-empty -m "main init"
  git -C "$main" -c protocol.file.allow=always submodule add -q "$sub" subdir
  git -C "$main/subdir" checkout -q "$sha1"
  git -C "$main" add subdir
  git -C "$main" commit -q -m "add submodule @ sha1"
  git -C "$main" branch -q topic
  git -C "$main" checkout -q topic
  git -C "$main/subdir" checkout -q "$sha2"
  git -C "$main" add subdir
  git -C "$main" commit -q -m "advance gitlink to sha2"
  git -C "$main" checkout -q master
  (cd "$main" && cmd_init "$main" >/dev/null)
  printf '%s %s %s\n' "$main" "$sha1" "$sha2"
}

board_count() { # board_count <root> <state> -> number of .md cards in that state
  local root=$1 state=$2 kb
  kb=$(kanban_board_dir "$root")
  local -a files=("$kb/$state"/*.md)
  [[ -e ${files[0]} ]] || { echo 0; return; }
  echo "${#files[@]}"
}

# --- functions under test read $KB directly (they are normally only ever
# reached after cmd_run's require_root already populated it); the test
# harness has to do the same before calling them standalone.
run_enqueue() { # run_enqueue <root> <topic> <base>
  local root=$1 topic=$2 base=$3
  ( cd "$root" && ROOT=$root && KB=$(kanban_board_dir "$root") && enqueue_submodule_publish_cards "$topic" "$base" )
}

# --- scenario 1: a gitlink change on topic vs base files exactly one
# backlog card, whose body carries path, new sha, and the publish-procedure
# doc reference.
test_enqueues_backlog_card_with_path_and_sha() {
  local dir=$WORKDIR/s1 root sha1 sha2 kb card
  mkdir -p "$dir"
  read -r root sha1 sha2 < <(build_fixture "$dir")
  ROOT=$root
  kb=$(kanban_board_dir "$root")
  run_enqueue "$root" topic master
  probe test "$(board_count "$root" backlog)" = 1
  assert_true "one backlog card is filed for the changed submodule" "$STATUS"
  card=$(ls "$kb/backlog"/*.md 2>/dev/null | head -1)
  probe test -n "$card"
  assert_true "backlog card file exists" "$STATUS"
  probe grep -q "subdir" "$card"
  assert_true "card body mentions the submodule path" "$STATUS"
  probe grep -q "$sha2" "$card"
  assert_true "card body mentions the new sha" "$STATUS"
  probe grep -q "submodule-commit.md" "$card"
  assert_true "card body references the packaged/non-packaged publish procedure doc" "$STATUS"
}

# --- scenario 2: no gitlink change between topic and base files nothing.
test_no_gitlink_change_enqueues_nothing() {
  local dir=$WORKDIR/s2 root sha1 sha2
  mkdir -p "$dir"
  read -r root sha1 sha2 < <(build_fixture "$dir")
  ROOT=$root
  run_enqueue "$root" master master
  assert_eq "no cards filed when topic == base (no gitlink change)" 0 "$(board_count "$root" backlog)"
}

# --- scenario 3: an undispatched publish card for the same submodule
# already sitting in backlog or todo suppresses a duplicate.
test_pending_card_in_backlog_suppresses_duplicate() {
  local dir=$WORKDIR/s3 root sha1 sha2
  mkdir -p "$dir"
  read -r root sha1 sha2 < <(build_fixture "$dir")
  ROOT=$root
  run_enqueue "$root" topic master
  run_enqueue "$root" topic master
  assert_eq "re-running enqueue does not duplicate a pending backlog card" 1 "$(board_count "$root" backlog)"
}

test_pending_card_in_todo_suppresses_duplicate() {
  local dir=$WORKDIR/s4 root sha1 sha2 kb card
  mkdir -p "$dir"
  read -r root sha1 sha2 < <(build_fixture "$dir")
  ROOT=$root
  kb=$(kanban_board_dir "$root")
  run_enqueue "$root" topic master
  card=$(ls "$kb/backlog"/*.md 2>/dev/null | head -1)
  mv "$card" "$kb/todo/"
  run_enqueue "$root" topic master
  assert_eq "a pending todo card also suppresses a duplicate (backlog stays empty)" 0 "$(board_count "$root" backlog)"
}

# --- scenario 4: once the earlier card has moved past backlog/todo (e.g. it
# reached done because the submodule was actually published), a fresh merge
# that changes the gitlink again is free to file a new card.
test_card_past_todo_does_not_suppress_new_card() {
  local dir=$WORKDIR/s5 root sha1 sha2 kb card
  mkdir -p "$dir"
  read -r root sha1 sha2 < <(build_fixture "$dir")
  ROOT=$root
  kb=$(kanban_board_dir "$root")
  run_enqueue "$root" topic master
  card=$(ls "$kb/backlog"/*.md 2>/dev/null | head -1)
  mv "$card" "$kb/done/"
  run_enqueue "$root" topic master
  assert_eq "a card already past backlog/todo does not block a new one" 1 "$(board_count "$root" backlog)"
}

# --- scenario 5: the OPERATOR CONTRACT worker prompt now tells the operator
# to confirm submodule publish state before pushing the parent repo.
test_operator_contract_mentions_submodule_publish_check() {
  local dir=$WORKDIR/s6 root card prompt
  mkdir -p "$dir"
  root=$dir/main
  git init -q "$root"
  git -C "$root" commit -q --allow-empty -m init
  (cd "$root" && cmd_init "$root" >/dev/null)
  card=$(cd "$root" && printf 'publish the thing\n' | cmd_add "publish something" --operate)
  prompt=$(worker_prompt_for_card "$card")
  probe grep -qi "submodule" <<<"$prompt"
  assert_true "OPERATOR CONTRACT prompt mentions submodule publish confirmation" "$STATUS"
  probe grep -qi "push" <<<"$prompt"
  assert_true "OPERATOR CONTRACT prompt mentions pushing the parent repo" "$STATUS"
}

test_enqueues_backlog_card_with_path_and_sha
test_no_gitlink_change_enqueues_nothing
test_pending_card_in_backlog_suppresses_duplicate
test_pending_card_in_todo_suppresses_duplicate
test_card_past_todo_does_not_suppress_new_card
test_operator_contract_mentions_submodule_publish_check

note ""
note "pass=$pass_count fail=$fail_count"
[[ $fail_count -eq 0 ]]
