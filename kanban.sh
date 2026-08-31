#!/usr/bin/env bash
# kanban.sh - file-based kanban dispatcher for agent workers.
# Cards live in <project>/.kanban/{todo,doing,review,done,failed}/ as Markdown
# with YAML frontmatter. `kanban run` executes cards via a worker backend
# (claude / codex, or a visible Herdr wrapper), then scores the result with a
# review agent and loops until the score passes the threshold or attempts run out.
# In a git repository each card runs in its own worktree on branch
# kanban/<id>, so `kanban run -j N` processes N cards in parallel; passing
# work is merged back into the base branch (merges are serialized).
# See README.md for the workflow contract.
set -euo pipefail

STATES=(todo doing review done failed)
DEFAULT_THRESHOLD=80
DEFAULT_MAX_ATTEMPTS=3
DEFAULT_BACKEND=auto
DEFAULT_MODEL=""
BACKENDS="claude codex"

die() { echo "kanban: $*" >&2; exit 1; }

find_root() {
  local d=$PWD
  while [[ $d != / ]]; do
    if [[ -d $d/.kanban ]]; then echo "$d"; return 0; fi
    d=$(dirname "$d")
  done
  return 1
}

require_root() {
  ROOT=$(find_root) || die "no .kanban directory found (run: kanban init)"
  KB=$ROOT/.kanban
  load_project_config
}

cfg_env() { # cfg_env <file> <key> <env-name>: env wins; else adopt non-empty cfg value
  local v
  v=$(fm_get "$1" "$2" "")
  if [[ -n $v ]] && ! eval "[[ -n \${$3:-} ]]"; then
    eval "export $3=\"\$v\""
  fi
}

load_project_config() { # .kanban/KANBAN.md frontmatter -> defaults (env still wins)
  local cfg=$KB/KANBAN.md
  [[ -f $cfg ]] || return 0
  DEFAULT_BACKEND=$(fm_get "$cfg" default_backend "$DEFAULT_BACKEND")
  DEFAULT_MODEL=$(fm_get "$cfg" default_model "$DEFAULT_MODEL")
  DEFAULT_THRESHOLD=$(fm_get "$cfg" threshold "$DEFAULT_THRESHOLD")
  DEFAULT_MAX_ATTEMPTS=$(fm_get "$cfg" max_attempts "$DEFAULT_MAX_ATTEMPTS")
  cfg_env "$cfg" backend_order KANBAN_BACKEND_ORDER
  cfg_env "$cfg" reviewer KANBAN_REVIEWER
  cfg_env "$cfg" review_model KANBAN_REVIEW_MODEL
  cfg_env "$cfg" jobs KANBAN_JOBS
  cfg_env "$cfg" claude_perms KANBAN_CLAUDE_PERMS
  cfg_env "$cfg" codex_sandbox KANBAN_CODEX_SANDBOX
}

fm_get() { # fm_get <file> <key> <default>
  local v
  v=$(awk -v k="$2" 'NR==1&&$0!="---"{exit} /^---$/{c++;next} c==1&&index($0,k": ")==1{print substr($0,length(k)+3);exit}' "$1")
  echo "${v:-$3}"
}

fm_set() { # fm_set <file> <key> <value>
  python3 - "$1" "$2" "$3" <<'EOF'
import sys
path, key, value = sys.argv[1:4]
lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
out, in_fm, done, seen = [], False, False, 0
for line in lines:
    if line == "---" and seen < 2:
        seen += 1
        in_fm = seen == 1
        if seen == 2 and not done:
            out.append(f"{key}: {value}")
            done = True
    elif in_fm and line.startswith(key + ":") and not done:
        line = f"{key}: {value}"
        done = True
    out.append(line)
open(path, "w").write("\n".join(out))
EOF
}

card_body() { # everything after frontmatter
  awk 'c==2{print} /^---$/{c++}' "$1"
}

append_history() { # append_history <file> <heading> ; body from stdin
  # iconv -c: worker output can carry invalid UTF-8 (terminal control bytes);
  # once they land in the card, every later python read of it explodes.
  {
    echo ""
    echo "### $(date '+%Y-%m-%d %H:%M:%S') $2"
    echo ""
    iconv -f UTF-8 -t UTF-8 -c 2>/dev/null || cat
  } >>"$1"
}

move_card() { # move_card <file> <state> -> echoes new path
  local dest=$KB/$2/$(basename "$1")
  mv "$1" "$dest"
  echo "$dest"
}

cmd_init() {
  local base=${1:-$PWD}/.kanban
  for s in "${STATES[@]}"; do mkdir -p "$base/$s"; done
  touch "$base"/{todo,doing,review,done,failed}/.gitkeep
  printf 'wt/\n.lock\n.merge.lock\n' >"$base/.gitignore"
  if [[ ! -f $base/KANBAN.md ]]; then
    cat >"$base/KANBAN.md" <<'EOF'
---
backend_order: claude codex
default_backend: auto
default_model: sonnet
reviewer: auto
review_model: sonnet
threshold: 80
max_attempts: 3
jobs: 2
claude_perms: acceptEdits
codex_sandbox: workspace-write
---

# このプロジェクトのカンバン運用ポリシー

秘書 (対話) エージェントはカードを切る前にこのファイルを読み、以下に従うこと。
frontmatter は kanban CLI が既定値として読む (環境変数が優先)。

## エージェント・モデル構成

- **既定方針: 上位モデル (fable / opus 等) は秘書・設計役だけ。手を動かすワーカーとレビュワーは下位モデルで十分**
- 既定: 通常実装は claude / sonnet、軽微な修正は codex / gpt-5.3-codex-spark (codex カードは -m 必須。model 名はバックエンド固有)
- 設計・難所のカードだけ例外的に -m opus 等へ上げる (理由をカードに書く)

## カードの切り方

- (例) ファイル境界で分割し、同一ファイルを触るカードは同時に投入しない
- (例) 完了条件と検証コマンドを必ずカード本文に書く

## ディスパッチャ運用

- 秘書開始時は `$kanban-dispatch 秘書として開始` を使う。スキルが環境を実測し、以後の会話では対話エージェント自身が実装しない
- Herdr 環境ではカード追加後に `~/git/MornKanban/kanban-secretary.sh dispatch` を使う。bare `kanban run` へ置き換えない
- visible Herdr が利用不能なら、ユーザーが headless を明示しない限り勝手にフォールバックしない
- (例) failed カードは秘書がユーザーへ即報告する
EOF
  fi
  echo "initialized $base"
}

cmd_add() {
  require_root
  local title="" backend=$DEFAULT_BACKEND model=$DEFAULT_MODEL threshold=$DEFAULT_THRESHOLD
  while [[ $# -gt 0 ]]; do
    case $1 in
      -b|--backend) backend=$2; shift 2 ;;
      -m|--model) model=$2; shift 2 ;;
      -t|--threshold) threshold=$2; shift 2 ;;
      *) title=$1; shift ;;
    esac
  done
  [[ -n $title ]] || die "usage: kanban add \"title\" [-b claude|codex|auto] [-m model] [-t threshold] < description"
  case $backend in
    auto|claude|codex) ;;
    *) die "unknown backend: $backend (auto|claude|codex)" ;;
  esac
  local desc
  if [[ ! -t 0 ]]; then desc=$(cat); else desc=$title; fi
  local id slug file
  id=$(date +%Y%m%d-%H%M%S)-$RANDOM
  slug=$(echo "$title" | tr -cs '[:alnum:]' '-' | tr '[:upper:]' '[:lower:]' | cut -c1-40 | sed 's/-$//')
  file=$KB/todo/$id-${slug:-task}.md
  cat >"$file" <<EOF
---
id: $id
title: $title
backend: $backend
model: $model
threshold: $threshold
max_attempts: $DEFAULT_MAX_ATTEMPTS
attempts: 0
created: $(date '+%Y-%m-%dT%H:%M:%S')
---

## Task

$desc

## History
EOF
  echo "$file"
}

cmd_list() {
  require_root
  for s in "${STATES[@]}"; do
    local files=("$KB/$s"/*.md)
    [[ -e ${files[0]} ]] || continue
    echo "[$s]"
    for f in "${files[@]}"; do
      printf '  %s  %s (attempts: %s)\n' "$(fm_get "$f" id ?)" "$(fm_get "$f" title ?)" "$(fm_get "$f" attempts 0)"
    done
  done
}

cmd_show() {
  require_root
  local hits=("$KB"/*/*"$1"*.md)
  [[ -e ${hits[0]} ]] || die "no card matching '$1'"
  cat "${hits[0]}"
}

resolve_backend() { # echo first installed backend from KANBAN_BACKEND_ORDER
  local b
  for b in ${KANBAN_BACKEND_ORDER:-$BACKENDS}; do
    if command -v "$b" >/dev/null 2>&1; then echo "$b"; return 0; fi
  done
  return 1
}

worker_cmd() { # worker_cmd <backend> <model> (model may be empty = backend default)
  if [[ -n ${KANBAN_WORKER_CMD:-} ]]; then echo "$KANBAN_WORKER_CMD"; return; fi
  local b=$1
  if [[ $b == auto ]]; then b=$(resolve_backend) || die "no agent CLI found (order: ${KANBAN_BACKEND_ORDER:-$BACKENDS})"; fi
  case $b in
    claude) echo "claude -p${2:+ --model $2} --permission-mode ${KANBAN_CLAUDE_PERMS:-acceptEdits}" ;;
    codex) echo "codex exec --skip-git-repo-check -s ${KANBAN_CODEX_SANDBOX:-workspace-write}${2:+ -m $2}" ;;
    *) die "unknown backend: $b" ;;
  esac
}

review_cmd() {
  if [[ -n ${KANBAN_REVIEW_CMD:-} ]]; then echo "$KANBAN_REVIEW_CMD"; return; fi
  local b=${KANBAN_REVIEWER:-auto} m=${KANBAN_REVIEW_MODEL:-}
  if [[ $b == auto ]]; then b=$(resolve_backend) || die "no agent CLI found (order: ${KANBAN_BACKEND_ORDER:-$BACKENDS})"; fi
  case $b in
    claude) echo "claude -p${m:+ --model $m}" ;;
    codex) echo "codex exec --skip-git-repo-check -s read-only${m:+ -m $m}" ;;
    *) die "unknown reviewer backend: $b" ;;
  esac
}

parse_score() { # stdin: reviewer output -> "score<TAB>feedback" (empty on failure)
  python3 -c '
import json, re, sys
text = sys.stdin.read()
for m in reversed(re.findall(r"\{[^{}]*\}", text, re.S)):
    try:
        d = json.loads(m)
        if "score" in d:
            fb = str(d.get("feedback", "")).replace("\t", " ").replace("\n", " ")
            print(str(int(d["score"])) + "\t" + fb)
            sys.exit(0)
    except (ValueError, TypeError):
        continue
'
}

run_attempt() { # run_attempt <card> <workdir> -> sets ATT_SCORE / ATT_FEEDBACK
  local file=$1 workdir=$2
  local backend model wcmd out title
  backend=$(fm_get "$file" backend "$DEFAULT_BACKEND")
  model=$(fm_get "$file" model "")
  title=$(fm_get "$file" title "")
  wcmd=$(worker_cmd "$backend" "$model")
  # Custom worker commands (KANBAN_WORKER_CMD) receive the card's routing
  # via env, since the override bypasses worker_cmd's model handling.
  out=$( (cd "$workdir" && card_body "$file" |
    KANBAN_CARD_MODEL=$model KANBAN_CARD_BACKEND=$backend KANBAN_CARD_TITLE=$title $wcmd 2>&1) ) || true
  echo "$out" | tail -n 40 | append_history "$file" "worker output (tail)"

  local rcmd review_out parsed
  rcmd=$(review_cmd)
  review_out=$( (cd "$workdir" && KANBAN_CARD_TITLE=$title $rcmd 2>&1 <<EOF
You are a strict reviewer. Inspect this repository's current state and judge
whether the task below is genuinely complete and of good quality. Check the
actual files and diffs; do not trust the worker's claims.

$(card_body "$file")

Output ONLY a JSON object: {"score": <0-100>, "feedback": "<what is missing or wrong, concretely>"}
EOF
  ) ) || true
  parsed=$(echo "$review_out" | parse_score)
  if [[ -z $parsed ]]; then
    ATT_SCORE=0
    ATT_FEEDBACK="reviewer output was not parseable JSON: $(echo "$review_out" | tail -c 200)"
  else
    ATT_SCORE=${parsed%%$'\t'*}
    ATT_FEEDBACK=${parsed#*$'\t'}
  fi
}

record_attempt() { # record_attempt <card> <threshold> -> increments attempts
  local file=$1 threshold=$2 attempts
  attempts=$(($(fm_get "$file" attempts 0) + 1))
  fm_set "$file" attempts "$attempts"
  printf 'score: %s / threshold: %s\n\n%s\n' "$ATT_SCORE" "$threshold" "$ATT_FEEDBACK" |
    append_history "$file" "review"
}

notify_result() { # notify_result <done|failed> <title> ; optional hook, never fatal
  if [[ -n ${KANBAN_NOTIFY_CMD:-} ]]; then
    $KANBAN_NOTIFY_CMD "$1" "$2" >/dev/null 2>&1 || true
  fi
}

merge_lock() { # merge_lock <acquire|release>
  local lock=$KB/.merge.lock
  if [[ $1 == acquire ]]; then
    local i=0
    until mkdir "$lock" 2>/dev/null; do
      sleep 1
      i=$((i + 1))
      # if-form: `[[ ]] && die` returns 1 when false and trips errexit (bash 3.2)
      if [[ $i -gt 600 ]]; then die "merge lock timed out"; fi
    done
  else
    rmdir "$lock" 2>/dev/null || true
  fi
}

process_card_seq() { # non-git fallback: run in place, retry via todo
  local file=$1
  local title threshold max_attempts attempts
  title=$(fm_get "$file" title "?")
  threshold=$(fm_get "$file" threshold "$DEFAULT_THRESHOLD")
  max_attempts=$(fm_get "$file" max_attempts "$DEFAULT_MAX_ATTEMPTS")
  attempts=$(fm_get "$file" attempts 0)

  echo "==> [$title] attempt $((attempts + 1))/$max_attempts"
  run_attempt "$file" "$ROOT"
  record_attempt "$file" "$threshold"
  attempts=$((attempts + 1))

  if [[ $ATT_SCORE -ge $threshold ]]; then
    move_card "$file" done >/dev/null
    echo "    PASS score=$ATT_SCORE -> done"
    notify_result done "$title"
  elif [[ $attempts -ge $max_attempts ]]; then
    move_card "$file" failed >/dev/null
    echo "    FAIL score=$ATT_SCORE attempts exhausted -> failed (needs human)"
    notify_result failed "$title"
  else
    printf '%s\n' "$ATT_FEEDBACK" | append_history "$file" "rework instruction (fix these points)"
    move_card "$file" todo >/dev/null
    echo "    RETRY score=$ATT_SCORE -> todo"
  fi
}

process_card_wt() { # git mode: own worktree/branch, retries in place, merge on pass
  local file=$1 base_branch=$2
  local id title threshold max_attempts attempts
  id=$(fm_get "$file" id "?")
  title=$(fm_get "$file" title "?")
  threshold=$(fm_get "$file" threshold "$DEFAULT_THRESHOLD")
  max_attempts=$(fm_get "$file" max_attempts "$DEFAULT_MAX_ATTEMPTS")
  attempts=$(fm_get "$file" attempts 0)
  local branch=kanban/$id wt=$KB/wt/$id
  local tag="[$title]"

  if ! git -C "$ROOT" worktree add -q -b "$branch" "$wt" "$base_branch" 2>>"$KB/wt/$id.log"; then
    echo "worktree add failed; see .kanban/wt/$id.log" | append_history "$file" "error"
    move_card "$file" failed >/dev/null
    echo "$tag FAIL worktree add failed -> failed"
    notify_result failed "$title"
    return
  fi

  local passed=false
  while [[ $attempts -lt $max_attempts ]]; do
    echo "$tag attempt $((attempts + 1))/$max_attempts (branch: $branch)"
    run_attempt "$file" "$wt"
    git -C "$wt" add -A
    git -C "$wt" commit -q --allow-empty -m "kanban: $title (attempt $((attempts + 1)))"
    record_attempt "$file" "$threshold"
    attempts=$((attempts + 1))
    if [[ $ATT_SCORE -ge $threshold ]]; then passed=true; break; fi
    echo "$tag RETRY score=$ATT_SCORE"
    printf '%s\n' "$ATT_FEEDBACK" | append_history "$file" "rework instruction (fix these points)"
  done

  if ! $passed; then
    printf 'branch %s is kept for manual inspection.\n' "$branch" | append_history "$file" "gave up"
    git -C "$ROOT" worktree remove --force "$wt" 2>/dev/null || true
    move_card "$file" failed >/dev/null
    echo "$tag FAIL score=$ATT_SCORE attempts exhausted -> failed (branch $branch kept)"
    notify_result failed "$title"
    return
  fi

  merge_lock acquire
  if git -C "$ROOT" merge --no-ff -q -m "kanban: $title" "$branch" 2>>"$KB/wt/$id.log"; then
    merge_lock release
    git -C "$ROOT" worktree remove --force "$wt" 2>/dev/null || true
    git -C "$ROOT" branch -q -D "$branch" 2>/dev/null || true
    rm -f "$KB/wt/$id.log"
    move_card "$file" done >/dev/null
    echo "$tag PASS score=$ATT_SCORE -> done (merged into $base_branch)"
    notify_result done "$title"
  else
    git -C "$ROOT" merge --abort 2>/dev/null || true
    merge_lock release
    git -C "$ROOT" worktree remove --force "$wt" 2>/dev/null || true
    printf 'work passed review (score %s) but merging %s into %s failed; merge it manually.\n' \
      "$ATT_SCORE" "$branch" "$base_branch" | append_history "$file" "merge conflict"
    move_card "$file" failed >/dev/null
    echo "$tag CONFLICT -> failed (branch $branch kept; merge manually)"
    notify_result failed "$title"
  fi
}

cmd_run() {
  require_root
  local once=false jobs_max=${KANBAN_JOBS:-1}
  while [[ $# -gt 0 ]]; do
    case $1 in
      --once) once=true; shift ;;
      -j|--jobs) jobs_max=$2; shift 2 ;;
      *) die "usage: kanban run [--once] [-j N]" ;;
    esac
  done
  local lock=$KB/.lock
  if [[ -f $lock ]] && kill -0 "$(cat "$lock")" 2>/dev/null; then
    die "dispatcher already running (pid $(cat "$lock"))"
  fi
  echo $$ >"$lock"
  trap "rm -f '$lock'; rmdir '$KB/.merge.lock' 2>/dev/null || true" EXIT
  # Fail fast if no backend CLI is available (dies with a message here instead
  # of silently inside a background job).
  worker_cmd "$DEFAULT_BACKEND" "" >/dev/null
  review_cmd >/dev/null
  # Reclaim cards stranded by a crashed dispatcher (lock guarantees exclusivity).
  local orphan
  for orphan in "$KB"/doing/*.md "$KB"/review/*.md; do
    if [[ -e $orphan ]]; then move_card "$orphan" todo >/dev/null; fi
  done

  if ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    if [[ $jobs_max -gt 1 ]]; then die "parallel mode requires a git repository (worktrees)"; fi
    while :; do
      local cards=("$KB"/todo/*.md)
      [[ -e ${cards[0]} ]] || { echo "todo is empty"; break; }
      process_card_seq "$(move_card "${cards[0]}" doing)"
      if $once; then break; fi
    done
    return
  fi

  mkdir -p "$KB/wt"
  local base_branch
  base_branch=$(git -C "$ROOT" symbolic-ref --short HEAD) ||
    die "detached HEAD; check out a branch first"
  local spawned=0
  while :; do
    local running
    running=$(jobs -rp | wc -l | tr -d ' ')
    local cards=("$KB"/todo/*.md)
    if [[ -e ${cards[0]} && $running -lt $jobs_max ]] && { ! $once || [[ $spawned -eq 0 ]]; }; then
      local picked
      picked=$(move_card "${cards[0]}" doing)   # claim synchronously before spawning
      # Crash net: an unexpected error inside the job (set -e) must not strand
      # the card in doing/ silently — record it and move the card to failed.
      job_crash_net() { # job_crash_net <status> <picked>
        local st=$1 f=$KB/doing/$(basename "$2")
        if [[ $st -ne 0 && -f $f ]]; then
          local t
          t=$(fm_get "$f" title "?" 2>/dev/null || basename "$2")
          echo "job crashed unexpectedly (exit $st); see dispatcher output" | append_history "$f"
          mv "$f" "$KB/failed/"
          echo "[$t] CRASH exit=$st -> failed"
        fi
      }
      if [[ -n ${KANBAN_DEBUG:-} ]]; then
        ( exec 2>"$KB/wt/job.$(basename "$picked").trace"; set -x
          trap 'job_crash_net $? "$picked"; echo "JOB EXIT status=$?" >&2' EXIT
          process_card_wt "$picked" "$base_branch" ) &
      else
        ( trap 'job_crash_net $? "$picked"' EXIT
          process_card_wt "$picked" "$base_branch" ) &
      fi
      spawned=$((spawned + 1))
      continue
    fi
    if [[ $running -eq 0 ]]; then
      if [[ ! -e ${cards[0]} ]] || $once; then break; fi
    fi
    sleep 2
  done
  wait
  echo "todo is empty"
}

case ${1:-} in
  init) shift; cmd_init "$@" ;;
  add) shift; cmd_add "$@" ;;
  list|ls) cmd_list ;;
  show) shift; cmd_show "${1:?usage: kanban show <id>}" ;;
  run) shift || true; cmd_run "$@" ;;
  *) die "usage: kanban {init|add|list|show|run [--once] [-j N]}" ;;
esac
