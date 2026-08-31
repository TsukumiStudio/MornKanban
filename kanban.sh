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

STATES=(todo doing review resolving blocked done failed)
DEFAULT_THRESHOLD=80
DEFAULT_MAX_ATTEMPTS=3
DEFAULT_RESOLVE_MAX_ATTEMPTS=2
DEFAULT_BACKEND=auto
DEFAULT_MODEL=""
BACKENDS="claude codex"

die() { echo "kanban: $*" >&2; exit 1; }

resolve_self_dir() { # follow symlinks (e.g. ~/.local/bin/kanban) to the real repo dir, bash 3.2 safe
  local src=$1 dir
  while [[ -L $src ]]; do
    dir=$(cd -P "$(dirname "$src")" && pwd)
    src=$(readlink "$src")
    case $src in
      /*) ;;
      *) src=$dir/$src ;;
    esac
  done
  cd -P "$(dirname "$src")" && pwd
}

SELF_DIR=$(resolve_self_dir "$0")
VERSION_FILE=$SELF_DIR/VERSION
SETUP_CLI=$SELF_DIR/gui/setup_cli.py

cmd_version() { python3 "$SETUP_CLI" version; }
cmd_install() { python3 "$SETUP_CLI" install; }
cmd_update() { python3 "$SETUP_CLI" update; }
cmd_uninstall() { python3 "$SETUP_CLI" uninstall; }
cmd_monitor() { exec python3 "$SELF_DIR/monitor/cli.py" "$@"; }

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
  DEFAULT_RESOLVE_MAX_ATTEMPTS=$(fm_get "$cfg" resolve_max_attempts "$DEFAULT_RESOLVE_MAX_ATTEMPTS")
  cfg_env "$cfg" backend_order KANBAN_BACKEND_ORDER
  cfg_env "$cfg" reviewer KANBAN_REVIEWER
  cfg_env "$cfg" review_model KANBAN_REVIEW_MODEL
  cfg_env "$cfg" resolver KANBAN_RESOLVER
  cfg_env "$cfg" resolve_model KANBAN_RESOLVE_MODEL
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
resolver: auto
resolve_model: sonnet
threshold: 80
max_attempts: 3
resolve_max_attempts: 2
jobs: 2
claude_perms: acceptEdits
codex_sandbox: workspace-write
---

# このプロジェクトのカンバン運用ポリシー

秘書 (対話) エージェントはカードを切る前にこのファイルを読み、以下に従うこと。
frontmatter は kanban CLI が既定値として読む (環境変数が優先)。

## 秘書契約 (最重要)

- **秘書はファイル重複・依存順序・実行中カードとの競合を理由に起票を保留しない。**
  明白な自己完結情報が揃い次第、即座に `todo` へ追加して dispatcher へ渡す。
- 秘書は競合調査・rebase/merge・修正・検証を一切行わない。それらは実行側
  (dispatcher/worker/reviewer/resolver) の責務であり、正式な状態遷移で処理する。
- 順序依存やファイル競合の解決は実行時に判明してから実行側が処理する。秘書へ
  戻さない。

## エージェント・モデル構成

- **既定方針: 上位モデル (fable / opus 等) は秘書・設計役だけ。手を動かすワーカーとレビュワー・resolver は下位モデルで十分**
- 既定: 通常実装は claude / sonnet、軽微な修正は codex / gpt-5.3-codex-spark (codex カードは -m 必須。model 名はバックエンド固有)
- 設計・難所のカードだけ例外的に -m opus 等へ上げる (理由をカードに書く)
- resolver も既定では worker と同じ下位モデル (`resolver` / `resolve_model`)

## カードの切り方

- (例) ファイル境界で分割し、同一ファイルを触るカードは同時に投入する (競合は
  resolver が処理する。秘書は分割の目安に使うだけで、投入を止める理由にしない)
- (例) 完了条件と検証コマンドを必ずカード本文に書く

## ディスパッチャ運用

- 秘書開始時は `$kanban-dispatch 秘書として開始` を使う。スキルが環境を実測し、以後の会話では対話エージェント自身が実装しない
- Herdr 環境ではカード追加後に `~/git/MornKanban/kanban-secretary.sh dispatch` を使う。bare `kanban run` へ置き換えない
- visible Herdr が利用不能なら、ユーザーが headless を明示しない限り勝手にフォールバックしない
- (例) failed カードは秘書がユーザーへ即報告する。resolving/blocked は実行側が
  自律的に処理するので、秘書は failed に落ちた時だけ介入する
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
resolve_max_attempts: $DEFAULT_RESOLVE_MAX_ATTEMPTS
attempts: 0
resolve_attempts: 0
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

resolve_cmd() { # resolve_cmd <card-backend> <card-model> -> resolver invocation (editing role, like a worker)
  if [[ -n ${KANBAN_RESOLVE_CMD:-} ]]; then echo "$KANBAN_RESOLVE_CMD"; return; fi
  local b=${KANBAN_RESOLVER:-auto} m=${KANBAN_RESOLVE_MODEL:-}
  [[ $b == auto ]] && b=$1
  [[ -n $m ]] || m=$2
  if [[ $b == auto ]]; then b=$(resolve_backend) || die "no agent CLI found (order: ${KANBAN_BACKEND_ORDER:-$BACKENDS})"; fi
  case $b in
    claude) echo "claude -p${m:+ --model $m} --permission-mode ${KANBAN_CLAUDE_PERMS:-acceptEdits}" ;;
    codex) echo "codex exec --skip-git-repo-check -s ${KANBAN_CODEX_SANDBOX:-workspace-write}${m:+ -m $m}" ;;
    *) die "unknown resolver backend: $b" ;;
  esac
}

detect_blocked() { # detect_blocked <worker-output> -> sets BLOCKED_REASON (empty = not blocked)
  BLOCKED_REASON=""
  local first_line
  first_line=$(printf '%s\n' "$1" | sed -n '1p')
  case $first_line in
    BLOCKED:*) BLOCKED_REASON=${first_line#BLOCKED:} ;;
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

run_attempt() { # run_attempt <card> <workdir> -> sets ATT_SCORE / ATT_FEEDBACK / ATT_BLOCKED_REASON
  local file=$1 workdir=$2
  local backend model wcmd out title
  ATT_BLOCKED_REASON=""
  backend=$(fm_get "$file" backend "$DEFAULT_BACKEND")
  model=$(fm_get "$file" model "")
  title=$(fm_get "$file" title "")
  wcmd=$(worker_cmd "$backend" "$model")
  # Custom worker commands (KANBAN_WORKER_CMD) receive the card's routing
  # via env, since the override bypasses worker_cmd's model handling.
  out=$( (cd "$workdir" && card_body "$file" |
    KANBAN_CARD_MODEL=$model KANBAN_CARD_BACKEND=$backend KANBAN_CARD_TITLE=$title $wcmd 2>&1) ) || true
  echo "$out" | tail -n 40 | append_history "$file" "worker output (tail)"

  # A worker that discovers a real-time ordering dependency (e.g. it needs
  # another card's result that is not merged yet) signals it instead of
  # guessing; the dialogue secretary is never consulted for this. See
  # detect_blocked().
  detect_blocked "$out"
  ATT_BLOCKED_REASON=$BLOCKED_REASON
  if [[ -n $ATT_BLOCKED_REASON ]]; then
    ATT_SCORE=0
    ATT_FEEDBACK=""
    return
  fi

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

run_resolve_attempt() { # run_resolve_attempt <card> <resolve-workdir> <conflict-files> <base-branch> <card-branch> -> sets ATT_SCORE/ATT_FEEDBACK
  local file=$1 workdir=$2 conflict_files=$3 base_branch=$4 card_branch=$5
  local backend model wcmd out title prompt
  backend=$(fm_get "$file" backend "$DEFAULT_BACKEND")
  model=$(fm_get "$file" model "")
  title=$(fm_get "$file" title "")
  wcmd=$(resolve_cmd "$backend" "$model")
  prompt=$(printf 'You are the conflict-resolution role for MornKanban. Card branch %s passed review but conflicts with the current base branch %s. Resolve the conflict in this worktree, preserving the intent of BOTH sides -- never simply discard one side. Run any tests the task requires, then leave the tree conflict-free.\n\nConflicted files:\n%s\n\nOriginal task:\n%s\n' \
    "$card_branch" "$base_branch" "$conflict_files" "$(card_body "$file")")
  out=$( (cd "$workdir" && printf '%s' "$prompt" |
    KANBAN_CARD_MODEL=$model KANBAN_CARD_BACKEND=$backend KANBAN_CARD_TITLE=$title \
    KANBAN_CONFLICT_FILES=$conflict_files KANBAN_BASE_BRANCH=$base_branch KANBAN_CARD_BRANCH=$card_branch \
    $wcmd 2>&1) ) || true
  echo "$out" | tail -n 40 | append_history "$file" "resolver output (tail)"
  git -C "$workdir" add -A
  git -C "$workdir" commit -q --allow-empty -m "kanban: resolve conflict for $title"

  if git -C "$workdir" diff --name-only --diff-filter=U | grep -q .; then
    ATT_SCORE=0
    ATT_FEEDBACK="conflict markers remain unresolved after the resolver's attempt"
    return
  fi

  local rcmd review_out parsed
  rcmd=$(review_cmd)
  review_out=$( (cd "$workdir" && KANBAN_CARD_TITLE=$title $rcmd 2>&1 <<EOF
You are a strict reviewer. This worktree is the result of resolving a merge
conflict between card branch $card_branch and base branch $base_branch.
Inspect the actual files and diffs; do not trust the resolver's claims. Judge
whether BOTH sides' intent was preserved and the task below is genuinely
complete.

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

record_resolve_attempt() { # record_resolve_attempt <card> <threshold> -> increments resolve_attempts
  local file=$1 threshold=$2 attempts
  attempts=$(($(fm_get "$file" resolve_attempts 0) + 1))
  fm_set "$file" resolve_attempts "$attempts"
  printf 'score: %s / threshold: %s\n\n%s\n' "$ATT_SCORE" "$threshold" "$ATT_FEEDBACK" |
    append_history "$file" "resolve review"
}

process_resolve_wt() { # process_resolve_wt <card> <base_branch> <card_branch> <card_wt> <conflict_files>
  # Called when a card passed review but its branch conflicts with base at
  # merge time. Dedicated resolver role: never discards either side, keeps
  # both branches until it truly succeeds or gives up, and only ever merges
  # the resolve branch into base (never the original card branch again --
  # no double-merge).
  local file=$1 base_branch=$2 card_branch=$3 card_wt=$4 conflict_files=$5
  local id title threshold resolve_max_attempts resolve_attempts
  id=$(fm_get "$file" id "?")
  title=$(fm_get "$file" title "?")
  threshold=$(fm_get "$file" threshold "$DEFAULT_THRESHOLD")
  resolve_max_attempts=$(fm_get "$file" resolve_max_attempts "$DEFAULT_RESOLVE_MAX_ATTEMPTS")
  resolve_attempts=$(fm_get "$file" resolve_attempts 0)
  local resolve_branch=kanban-resolve/$id resolve_wt=$KB/wt/$id-resolve
  local tag="[$title]"

  move_card "$file" resolving >/dev/null
  file=$KB/resolving/$(basename "$file")
  git -C "$ROOT" worktree remove --force "$card_wt" 2>/dev/null || true

  if ! git -C "$ROOT" worktree add -q -b "$resolve_branch" "$resolve_wt" "$base_branch" 2>>"$KB/wt/$id.log"; then
    echo "resolve worktree add failed; see .kanban/wt/$id.log" | append_history "$file" "error"
    git -C "$ROOT" branch -q -D "$card_branch" 2>/dev/null || true
    move_card "$file" failed >/dev/null
    echo "$tag FAIL resolve worktree add failed -> failed"
    notify_result failed "$title"
    return
  fi
  git -C "$resolve_wt" merge --no-ff -q -m "kanban: merge $card_branch for conflict resolution" "$card_branch" \
    2>>"$KB/wt/$id.log" || true

  local resolved=false
  while [[ $resolve_attempts -lt $resolve_max_attempts ]]; do
    echo "$tag resolve attempt $((resolve_attempts + 1))/$resolve_max_attempts (branch: $resolve_branch)"
    run_resolve_attempt "$file" "$resolve_wt" "$conflict_files" "$base_branch" "$card_branch"
    record_resolve_attempt "$file" "$threshold"
    resolve_attempts=$((resolve_attempts + 1))
    if [[ $ATT_SCORE -ge $threshold ]]; then resolved=true; break; fi
    echo "$tag RESOLVE RETRY score=$ATT_SCORE"
    printf '%s\n' "$ATT_FEEDBACK" | append_history "$file" "rework instruction (fix these points)"
  done

  if ! $resolved; then
    printf 'conflict files: %s\nresolve branch %s and original card branch %s are kept for manual inspection.\n' \
      "$conflict_files" "$resolve_branch" "$card_branch" | append_history "$file" "gave up (conflict unresolved)"
    git -C "$ROOT" worktree remove --force "$resolve_wt" 2>/dev/null || true
    move_card "$file" failed >/dev/null
    echo "$tag FAIL resolve score=$ATT_SCORE attempts exhausted -> failed (branches $resolve_branch, $card_branch kept)"
    notify_result failed "$title"
    return
  fi

  merge_lock acquire
  if git -C "$ROOT" merge --no-ff -q -m "kanban: $title (conflict resolved)" "$resolve_branch" 2>>"$KB/wt/$id.log"; then
    merge_lock release
    git -C "$ROOT" worktree remove --force "$resolve_wt" 2>/dev/null || true
    git -C "$ROOT" branch -q -D "$resolve_branch" "$card_branch" 2>/dev/null || true
    rm -f "$KB/wt/$id.log"
    move_card "$file" done >/dev/null
    echo "$tag PASS resolve score=$ATT_SCORE -> done (merged into $base_branch)"
    notify_result done "$title"
  else
    git -C "$ROOT" merge --abort 2>/dev/null || true
    merge_lock release
    git -C "$ROOT" worktree remove --force "$resolve_wt" 2>/dev/null || true
    printf 'resolve passed review (score %s) but merging %s into %s failed; branches %s and %s kept for manual merge.\n' \
      "$ATT_SCORE" "$resolve_branch" "$base_branch" "$resolve_branch" "$card_branch" |
      append_history "$file" "merge conflict (post-resolve)"
    move_card "$file" failed >/dev/null
    echo "$tag CONFLICT (post-resolve) -> failed (branches kept; merge manually)"
    notify_result failed "$title"
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
  if [[ -n $ATT_BLOCKED_REASON ]]; then
    printf 'worker reported a real-time ordering dependency:%s\n' "$ATT_BLOCKED_REASON" |
      append_history "$file" "blocked"
    move_card "$file" blocked >/dev/null
    echo "    BLOCKED ->$ATT_BLOCKED_REASON -> blocked (reclaimed on next dispatcher pass)"
    return
  fi
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
    if [[ -n $ATT_BLOCKED_REASON ]]; then
      printf 'worker reported a real-time ordering dependency:%s\nworktree is discarded; the card restarts on a fresh worktree from the next pickup.\n' \
        "$ATT_BLOCKED_REASON" | append_history "$file" "blocked"
      git -C "$ROOT" worktree remove --force "$wt" 2>/dev/null || true
      git -C "$ROOT" branch -q -D "$branch" 2>/dev/null || true
      move_card "$file" blocked >/dev/null
      echo "$tag BLOCKED ->$ATT_BLOCKED_REASON -> blocked"
      return
    fi
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
    local conflict_files
    conflict_files=$(git -C "$ROOT" diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ' ')
    git -C "$ROOT" merge --abort 2>/dev/null || true
    merge_lock release
    printf 'work passed review (score %s) but merging %s into %s conflicted on: %s\nhanding off to the resolver role instead of failing immediately.\n' \
      "$ATT_SCORE" "$branch" "$base_branch" "$conflict_files" | append_history "$file" "merge conflict"
    process_resolve_wt "$file" "$base_branch" "$branch" "$wt" "$conflict_files"
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
  # resolving/blocked cards also fold back their leftover worktree/branch so
  # the card restarts clean on its next pickup instead of colliding with
  # `git worktree add -b` on a name that already exists.
  local orphan is_git=false
  git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1 && is_git=true
  for orphan in "$KB"/doing/*.md "$KB"/review/*.md; do
    if [[ -e $orphan ]]; then move_card "$orphan" todo >/dev/null; fi
  done
  for orphan in "$KB"/resolving/*.md; do
    if [[ -e $orphan ]]; then
      local rid
      rid=$(fm_get "$orphan" id "?")
      if $is_git && [[ $rid != "?" ]]; then
        git -C "$ROOT" worktree remove --force "$KB/wt/$rid-resolve" 2>/dev/null || true
        git -C "$ROOT" branch -q -D "kanban-resolve/$rid" 2>/dev/null || true
        git -C "$ROOT" worktree remove --force "$KB/wt/$rid" 2>/dev/null || true
        git -C "$ROOT" branch -q -D "kanban/$rid" 2>/dev/null || true
      fi
      move_card "$orphan" todo >/dev/null
    fi
  done
  for orphan in "$KB"/blocked/*.md; do
    if [[ -e $orphan ]]; then
      local bid
      bid=$(fm_get "$orphan" id "?")
      if $is_git && [[ $bid != "?" ]]; then
        git -C "$ROOT" worktree remove --force "$KB/wt/$bid" 2>/dev/null || true
        git -C "$ROOT" branch -q -D "kanban/$bid" 2>/dev/null || true
      fi
      move_card "$orphan" todo >/dev/null
    fi
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
        local st=$1 base=$(basename "$2") f
        if [[ $st -ne 0 ]]; then
          for f in "$KB/doing/$base" "$KB/resolving/$base"; do
            if [[ -f $f ]]; then
              local t
              t=$(fm_get "$f" title "?" 2>/dev/null || basename "$2")
              echo "job crashed unexpectedly (exit $st); see dispatcher output" | append_history "$f"
              mv "$f" "$KB/failed/"
              echo "[$t] CRASH exit=$st -> failed"
            fi
          done
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
  monitor) shift || true; cmd_monitor "$@" ;;
  --version) cat "$VERSION_FILE" ;;
  version) cmd_version ;;
  install) cmd_install ;;
  update) cmd_update ;;
  uninstall) cmd_uninstall ;;
  *) die "usage: kanban {init|add|list|show|run [--once] [-j N]|monitor [run|daemon|config]|install|update|uninstall|version|--version}" ;;
esac
