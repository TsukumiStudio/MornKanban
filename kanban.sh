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
# A reviewer/resolver-reviewer that never returns a valid score (pane lost,
# agent_not_found, timeout, wrapper/tool error) is an infrastructure failure,
# not a quality verdict -- it must not burn a worker attempt. This retry
# count is tracked separately from `attempts` (see review_with_infra_retry).
DEFAULT_REVIEW_INFRA_MAX_RETRIES=2
DEFAULT_REVIEW_INFRA_BACKOFF_SECONDS=2
DEFAULT_DIAGNOSIS_TARGET_MINUTES=5
DEFAULT_DIAGNOSIS_MAX_MINUTES=10
# review_enabled priority: card override > environment > project policy > true.
PROJECT_REVIEW_ENABLED=""

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
REGISTRY_CLI=$SELF_DIR/registry/cli.py

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
  if [[ ! -f $cfg ]]; then
    [[ -n ${KANBAN_REVIEW_INFRA_MAX_RETRIES:-} ]] && DEFAULT_REVIEW_INFRA_MAX_RETRIES=$KANBAN_REVIEW_INFRA_MAX_RETRIES
    [[ -n ${KANBAN_REVIEW_INFRA_BACKOFF_SECONDS:-} ]] && DEFAULT_REVIEW_INFRA_BACKOFF_SECONDS=$KANBAN_REVIEW_INFRA_BACKOFF_SECONDS
    return 0
  fi
  DEFAULT_BACKEND=$(fm_get "$cfg" default_backend "$DEFAULT_BACKEND")
  DEFAULT_MODEL=$(fm_get "$cfg" default_model "$DEFAULT_MODEL")
  DEFAULT_THRESHOLD=$(fm_get "$cfg" threshold "$DEFAULT_THRESHOLD")
  DEFAULT_MAX_ATTEMPTS=$(fm_get "$cfg" max_attempts "$DEFAULT_MAX_ATTEMPTS")
  DEFAULT_RESOLVE_MAX_ATTEMPTS=$(fm_get "$cfg" resolve_max_attempts "$DEFAULT_RESOLVE_MAX_ATTEMPTS")
  DEFAULT_REVIEW_INFRA_MAX_RETRIES=$(fm_get "$cfg" review_infra_max_retries "$DEFAULT_REVIEW_INFRA_MAX_RETRIES")
  DEFAULT_REVIEW_INFRA_BACKOFF_SECONDS=$(fm_get "$cfg" review_infra_backoff_seconds "$DEFAULT_REVIEW_INFRA_BACKOFF_SECONDS")
  DEFAULT_DIAGNOSIS_TARGET_MINUTES=$(fm_get "$cfg" diagnosis_target_minutes "$DEFAULT_DIAGNOSIS_TARGET_MINUTES")
  DEFAULT_DIAGNOSIS_MAX_MINUTES=$(fm_get "$cfg" diagnosis_max_minutes "$DEFAULT_DIAGNOSIS_MAX_MINUTES")
  # env wins over project config for both (bounded-retry knobs are also
  # useful to override ad hoc, e.g. from a test or a CI job).
  [[ -n ${KANBAN_REVIEW_INFRA_MAX_RETRIES:-} ]] && DEFAULT_REVIEW_INFRA_MAX_RETRIES=$KANBAN_REVIEW_INFRA_MAX_RETRIES
  [[ -n ${KANBAN_REVIEW_INFRA_BACKOFF_SECONDS:-} ]] && DEFAULT_REVIEW_INFRA_BACKOFF_SECONDS=$KANBAN_REVIEW_INFRA_BACKOFF_SECONDS
  cfg_env "$cfg" backend_order KANBAN_BACKEND_ORDER
  cfg_env "$cfg" reviewer KANBAN_REVIEWER
  cfg_env "$cfg" review_model KANBAN_REVIEW_MODEL
  cfg_env "$cfg" resolver KANBAN_RESOLVER
  cfg_env "$cfg" resolve_model KANBAN_RESOLVE_MODEL
  cfg_env "$cfg" jobs KANBAN_JOBS
  cfg_env "$cfg" claude_perms KANBAN_CLAUDE_PERMS
  cfg_env "$cfg" codex_sandbox KANBAN_CODEX_SANDBOX
  cfg_env "$cfg" codex_full_bypass KANBAN_CODEX_FULL_BYPASS
  cfg_env "$cfg" codex_approval KANBAN_CODEX_APPROVAL
  if [[ -n ${KANBAN_REVIEW_ENABLED:-} ]]; then
    KANBAN_REVIEW_ENABLED=$(parse_bool "$KANBAN_REVIEW_ENABLED" "KANBAN_REVIEW_ENABLED env var")
  fi
  local review_value
  review_value=$(fm_get "$cfg" review_enabled "")
  if [[ -n $review_value ]]; then
    PROJECT_REVIEW_ENABLED=$(parse_bool "$review_value" "KANBAN.md review_enabled")
  fi
}

parse_bool() { # parse_bool <value> <context> -> true|false
  local raw=$1 context=$2 value
  value=$(echo "$raw" | tr '[:upper:]' '[:lower:]')
  case $value in
    true|1|yes|on) echo true ;;
    false|0|no|off) echo false ;;
    *) die "invalid boolean for $context: '$raw' (expected true or false)" ;;
  esac
}

resolve_card_review() { # resolve_card_review <card> -> CARD_REVIEW_ENABLED/SOURCE, persisted
  local file=$1 value
  value=$(fm_get "$file" review_enabled "auto")
  if [[ $value != auto ]]; then
    CARD_REVIEW_ENABLED=$(parse_bool "$value" "card review_enabled")
    CARD_REVIEW_SOURCE=$(fm_get "$file" review_source "card")
    return
  fi
  if [[ -n ${KANBAN_REVIEW_ENABLED:-} ]]; then
    CARD_REVIEW_ENABLED=$KANBAN_REVIEW_ENABLED
    CARD_REVIEW_SOURCE=env
  elif [[ -n $PROJECT_REVIEW_ENABLED ]]; then
    CARD_REVIEW_ENABLED=$PROJECT_REVIEW_ENABLED
    CARD_REVIEW_SOURCE=project
  else
    CARD_REVIEW_ENABLED=true
    CARD_REVIEW_SOURCE=default
  fi
  fm_set "$file" review_enabled "$CARD_REVIEW_ENABLED"
  fm_set "$file" review_source "$CARD_REVIEW_SOURCE"
  echo "review decision: review_enabled=$CARD_REVIEW_ENABLED (source: $CARD_REVIEW_SOURCE)" |
    append_history "$file" "review policy"
}

effective_review_enabled() { # effective_review_enabled <card> -> true|false without writing
  local file=$1 value
  value=$(fm_get "$file" review_enabled "auto")
  if [[ $value != auto ]]; then
    parse_bool "$value" "card review_enabled"
  elif [[ -n ${KANBAN_REVIEW_ENABLED:-} ]]; then
    echo "$KANBAN_REVIEW_ENABLED"
  elif [[ -n $PROJECT_REVIEW_ENABLED ]]; then
    echo "$PROJECT_REVIEW_ENABLED"
  else
    echo true
  fi
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
  printf 'wt/\n.lock\n.merge.lock\nactivity.jsonl\nactivity.jsonl.lock\n' >"$base/.gitignore"
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
review_infra_max_retries: 2
review_infra_backoff_seconds: 2
review_enabled: true
jobs: 2
diagnosis_target_minutes: 5
diagnosis_max_minutes: 10
# 既定は無制限権限 (worker/reviewer/resolver 共通)。
# claude_perms: bypassPermissions -> `--dangerously-skip-permissions` (permission prompt 全skip)
# codex_full_bypass: true         -> `--dangerously-bypass-approvals-and-sandbox` (approval/sandbox 両方skip)
# 安全側へ戻す例: claude_perms: acceptEdits / codex_full_bypass: false + codex_sandbox: workspace-write + codex_approval: on-request
claude_perms: bypassPermissions
codex_sandbox: danger-full-access
codex_full_bypass: true
codex_approval: never
# secretary_agent: secretary-my-project
---

# このプロジェクトのカンバン運用ポリシー

秘書 (対話) エージェントはカードを切る前にこのファイルを読み、以下に従うこと。
frontmatter は kanban CLI が既定値として読む (環境変数が優先)。

## review_enabled (reviewer審査の on/off)

- `review_enabled: true|false`。既定は `true`
- 優先順位: cardの `--review` / `--no-review` > `KANBAN_REVIEW_ENABLED` > この設定 > 既定
- `false` はworker成功後のreviewer審査だけを省略する。worker自身のtestは省略しない
- 決定値と出所はカードへ保存され、dispatcher再起動後も変わらない

## 調査カードのtimebox

- 調査・原因分析は `kanban add --diagnose` で起票し、原則5分目標・最大10分
- diagnoseはread-onlyの証拠・原因・次の一手の報告だけを成果とし、reviewer審査を既定で省略する
- 修正は診断後の別カード。ユーザーが明示的に同時修正を求めた時だけ通常の実装カードに含める
- 5分時点で結論をまとめ、10分以内に終わらない場合は途中成果を残して `BLOCKED: scope/timebox` とする

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

## worker/reviewer 権限ポリシー (UNRESTRICTED)【要リスク理解】

既定で worker/reviewer は **承認プロンプト・sandbox制限なし** (`UNRESTRICTED`) で起動する
(Claude: `--dangerously-skip-permissions` / Codex: `--dangerously-bypass-approvals-and-sandbox`)。
resolver ロールも同じ `claude_perms` / `codex_*` キーを使い、worker/reviewerと
同じ無制限権限で起動する。

- リスク: worktree 外のファイル・認証情報・ネットワーク・git remote・任意プロセスへ
  制限なくアクセスできる。信頼できないカード本文やプロンプトインジェクションを
  そのまま実行しうる
- 安全 mode へ戻す例 (このファイルの frontmatter で上書き):
  `claude_perms: acceptEdits` / `codex_full_bypass: false` + `codex_sandbox: workspace-write` + `codex_approval: on-request`
- 環境変数 `KANBAN_CLAUDE_PERMS` / `KANBAN_CODEX_SANDBOX` / `KANBAN_CODEX_FULL_BYPASS` /
  `KANBAN_CODEX_APPROVAL` が上記 frontmatter より優先する

## カードの切り方

- (例) ファイル境界で分割し、同一ファイルを触るカードは同時に投入する (競合は
  resolver が処理する。秘書は分割の目安に使うだけで、投入を止める理由にしない)
- (例) 完了条件と検証コマンドを必ずカード本文に書く

## 秘書エージェント名

- 既定の Herdr 秘書名は `secretary-<project-slug>` (project-slug は登録済みエイリアスまたは
  リポジトリ basename から生成、プロジェクトごとに固有・再現可能)。優先順位:
  環境変数 `KANBAN_HERDR_SECRETARY` > 上記 frontmatter の `secretary_agent:` > 生成された既定値
- 同じ basename の別プロジェクトなど名前が衝突する場合は `secretary_agent: <name>` を
  ここに書いて上書きする (詳細は README の Secretary Bootstrap / Herdr Integration)

## ディスパッチャ運用

- 秘書開始時は `$kanban-dispatch 秘書として開始` を使う。スキルが環境を実測し、以後の会話では対話エージェント自身が実装しない
- カード追加後は `~/git/MornKanban/kanban-secretary.sh dispatch` を使う。bare `kanban run` へ置き換えない
- Herdr は必須。実行モードを質問せず、利用不能なら停止・報告する。headless へフォールバックしない
- (例) failed カードは秘書がユーザーへ即報告する。resolving/blocked は実行側が
  自律的に処理するので、秘書は failed に落ちた時だけ介入する

## 秘書ペインの許可/禁止 (技術的ガード付き、詳細は MornKanban README の Secretary Guard)

**実装しない。検証しない。commit/push/tag しない。in-process agent を起動しない。カードを起票して visible Herdr へ dispatch する。**

- 許可: KANBAN.md/README/board の読み取り、read-only git (status/log/diff/show 等)、
  `kanban add/show/list/init/send`、`kanban-secretary.sh bootstrap/dispatch/end`、ユーザーへの報告
- 禁止: ファイルの直接編集・作成・削除、build/test/lint/formatter/server 起動、bare `kanban run`、
  headless agent CLI (`claude -p`/`codex exec`)、Claude/Codex の in-process Agent/Task/subagent、
  git の変更系全般 (add/commit/push/merge/rebase/reset/checkout/branch作成削除/tag/worktree等)、
  GitHub/GitLab 等の外部変更 (push/release/PR/issue/tag publish)、package publish、deploy
- Claude Code では上記の多くが `PreToolUse` フックで実行前に技術的に拒否される
  (`kanban-setup.sh` の状態表示や `kanban-secretary.sh bootstrap` の一行応答に `claude=enforced` 等が出る)。
  Codex は現状 `partial` (契約文言のみ)。ガードが拒否したら再確認を求めず、カード化して dispatch する
- 事故的に直接操作してしまったと気づいたら即停止し、自分でrollback/追加commit/push/tag削除をせず、
  ユーザーへ事実 (push/tag が既にリモートへ届いたか等) を報告し、監査・回収を別カードにする

## 秘書契約 (最重要): in-process delegation 禁止

秘書ペイン (bootstrap 済み) では、この CLI 自身の組み込みサブエージェント機能
(Claude Code の `Agent`/`Task`、Codex の collaboration/subagent 起動) を
**一切使わない**。visible Herdr pane を経由しない実装・調査・検証・レビュー・
競合解決は、カードもワークツリーも board 履歴も残らず契約違反になる。

- 許可: `.kanban/KANBAN.md` とボードの確認、`kanban add` / `kanban send`、
  `kanban-secretary.sh dispatch` / `dispatch --once`、ユーザーへの報告
- 禁止: `Agent`/`Task` (Claude Code)、collaboration/subagent 起動 (Codex)、
  `herdr-agent-worker.sh` 経由の visible pane を開かないその他の in-process delegation
- 違反に気づいたら即停止し、その成果は採用・merge せず、同じ依頼をカード化して
  dispatch へ回す
EOF
  fi
  echo "initialized $base"
}

cmd_add() {
  require_root
  local title="" backend=$DEFAULT_BACKEND model=$DEFAULT_MODEL threshold=$DEFAULT_THRESHOLD
  local review_enabled=auto review_source=auto task_kind=implementation
  while [[ $# -gt 0 ]]; do
    case $1 in
      -b|--backend) backend=$2; shift 2 ;;
      -m|--model) model=$2; shift 2 ;;
      -t|--threshold) threshold=$2; shift 2 ;;
      --review) review_enabled=true; review_source=card; shift ;;
      --no-review) review_enabled=false; review_source=card; shift ;;
      --diagnose) task_kind=diagnose; shift ;;
      *) title=$1; shift ;;
    esac
  done
  [[ -n $title ]] || die "usage: kanban add \"title\" [-b claude|codex|auto] [-m model] [-t threshold] [--review|--no-review] [--diagnose] < description"
  case $backend in
    auto|claude|codex) ;;
    *) die "unknown backend: $backend (auto|claude|codex)" ;;
  esac
  [[ $DEFAULT_DIAGNOSIS_TARGET_MINUTES =~ ^[1-9][0-9]*$ ]] || die "diagnosis_target_minutes must be a positive integer"
  [[ $DEFAULT_DIAGNOSIS_MAX_MINUTES =~ ^[1-9][0-9]*$ ]] || die "diagnosis_max_minutes must be a positive integer"
  [[ $DEFAULT_DIAGNOSIS_TARGET_MINUTES -le $DEFAULT_DIAGNOSIS_MAX_MINUTES ]] || die "diagnosis_target_minutes must not exceed diagnosis_max_minutes"
  if [[ $task_kind == diagnose && $review_enabled == auto ]]; then
    review_enabled=false
    review_source=diagnose
  fi
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
review_enabled: $review_enabled
review_source: $review_source
task_kind: $task_kind
diagnosis_target_minutes: $DEFAULT_DIAGNOSIS_TARGET_MINUTES
diagnosis_max_minutes: $DEFAULT_DIAGNOSIS_MAX_MINUTES
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

worker_prompt_for_card() { # worker_prompt_for_card <card>
  local file=$1 kind target maximum
  kind=$(fm_get "$file" task_kind implementation)
  if [[ $kind == diagnose ]]; then
    target=$(fm_get "$file" diagnosis_target_minutes "$DEFAULT_DIAGNOSIS_TARGET_MINUTES")
    maximum=$(fm_get "$file" diagnosis_max_minutes "$DEFAULT_DIAGNOSIS_MAX_MINUTES")
    cat <<EOF
DIAGNOSIS-ONLY TIMEBOX CONTRACT
- This card is read-only diagnosis, not implementation. Do not edit project files, commit, push, or broaden the scope.
- Target: summarize evidence and a likely cause within ${target} minutes. Hard maximum: ${maximum} minutes.
- Expected output: observed evidence, likely bottleneck/root cause, uncertainty, and one small follow-up implementation card if needed.
- At the target, stop expanding the investigation and write the best supported conclusion.
- If the hard maximum cannot be met, first preserve partial evidence in the answer, then make the answer's first line: BLOCKED: scope/timebox
- Do not add related benchmarks, UI, refactors, mutation tests, or fixes unless this card explicitly asks for them.

EOF
  fi
  card_body "$file"
}

cmd_list() {
  require_root
  for s in "${STATES[@]}"; do
    local files=("$KB/$s"/*.md)
    [[ -e ${files[0]} ]] || continue
    echo "[$s]"
    for f in "${files[@]}"; do
      local review_value review_label
      review_value=$(effective_review_enabled "$f")
      if [[ $review_value == false ]]; then review_label="Review: OFF (fast iteration)"; else review_label="Review: ON"; fi
      printf '  %s  %s (attempts: %s) [%s]\n' "$(fm_get "$f" id ?)" "$(fm_get "$f" title ?)" "$(fm_get "$f" attempts 0)" "$review_label"
    done
  done
}

cmd_show() {
  require_root
  local hits=("$KB"/*/*"$1"*.md)
  [[ -e ${hits[0]} ]] || die "no card matching '$1'"
  local review_value
  review_value=$(effective_review_enabled "${hits[0]}")
  if [[ $review_value == false ]]; then echo "Review: OFF (fast iteration)"; else echo "Review: ON"; fi
  cat "${hits[0]}"
}

resolve_backend() { # echo first installed backend from KANBAN_BACKEND_ORDER
  local b
  for b in ${KANBAN_BACKEND_ORDER:-$BACKENDS}; do
    if command -v "$b" >/dev/null 2>&1; then echo "$b"; return 0; fi
  done
  return 1
}

claude_perm_flag() { # claude_perm_flag -> echoes the CLI flag(s) for KANBAN_CLAUDE_PERMS
  local perms=${KANBAN_CLAUDE_PERMS:-bypassPermissions}
  if [[ $perms == bypassPermissions ]]; then
    echo "--dangerously-skip-permissions"
  else
    echo "--permission-mode $perms"
  fi
}

codex_sandbox_flag() { # codex_sandbox_flag -> echoes the CLI flag(s) for KANBAN_CODEX_*
  if [[ ${KANBAN_CODEX_FULL_BYPASS:-true} == true ]]; then
    echo "--dangerously-bypass-approvals-and-sandbox"
  else
    echo "-s ${KANBAN_CODEX_SANDBOX:-danger-full-access} -a ${KANBAN_CODEX_APPROVAL:-never}"
  fi
}

worker_cmd() { # worker_cmd <backend> <model> (model may be empty = backend default)
  if [[ -n ${KANBAN_WORKER_CMD:-} ]]; then echo "$KANBAN_WORKER_CMD"; return; fi
  local b=$1
  if [[ $b == auto ]]; then b=$(resolve_backend) || die "no agent CLI found (order: ${KANBAN_BACKEND_ORDER:-$BACKENDS})"; fi
  case $b in
    claude) echo "claude -p${2:+ --model $2} $(claude_perm_flag)" ;;
    codex) echo "codex exec --skip-git-repo-check $(codex_sandbox_flag)${2:+ -m $2}" ;;
    *) die "unknown backend: $b" ;;
  esac
}

review_cmd() {
  if [[ -n ${KANBAN_REVIEW_CMD:-} ]]; then echo "$KANBAN_REVIEW_CMD"; return; fi
  local b=${KANBAN_REVIEWER:-auto} m=${KANBAN_REVIEW_MODEL:-}
  if [[ $b == auto ]]; then b=$(resolve_backend) || die "no agent CLI found (order: ${KANBAN_BACKEND_ORDER:-$BACKENDS})"; fi
  case $b in
    claude) echo "claude -p${m:+ --model $m} $(claude_perm_flag)" ;;
    codex) echo "codex exec --skip-git-repo-check $(codex_sandbox_flag)${m:+ -m $m}" ;;
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
    claude) echo "claude -p${m:+ --model $m} $(claude_perm_flag)" ;;
    codex) echo "codex exec --skip-git-repo-check $(codex_sandbox_flag)${m:+ -m $m}" ;;
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

# review infrastructure failure classification -----------------------------
#
# A reviewer/resolver-reviewer invocation can fail for reasons that have
# nothing to do with the quality of the work under review: the visible Herdr
# pane it ran in can vanish (agent_not_found), the wrapper can time out, a
# tool call inside the reviewer agent can error out, or the reviewer can
# simply produce no output. None of that is a "score: 0" -- it is
# infrastructure flaking, and must not consume a worker attempt. See
# review_with_infra_retry().
#
# classify_review_infra_error is only ever consulted once parse_score has
# already failed to find a {"score": ...} object, so "real content" here
# just means: text that at least plausibly *is* an attempted review (which
# we treat conservatively as an infra/protocol problem too, since a
# reviewer that cannot produce parseable JSON is a broken reviewer, not a
# quality verdict) vs. one of the known lifecycle/wrapper failure shapes.
classify_review_infra_error() { # stdin: reviewer output (already failed parse_score) -> infra category
  python3 -c '
import re, sys
text = sys.stdin.read()

# Explicit sentinel emitted by the Herdr wrapper (herdr-agent-worker.sh) when
# it detects its own lifecycle failure -- the precise, non-heuristic case.
m = re.search(r"^KANBAN_INFRA_ERROR:\s*(.+)$", text, re.M)
if m:
    print(("wrapper_error: " + m.group(1).strip())[:200])
    sys.exit(0)

if not text.strip():
    print("empty_output")
    sys.exit(0)

# Known lifecycle/tooling failure shapes: pane or agent handle disappeared,
# the wrapper itself errored, a timeout was hit, or a tool call inside the
# reviewer errored out. Also guards against a terminal status line or a
# different cards leftover output being mistaken for review content.
sigs = [
    (r"agent target [^\n]* not found", "agent_not_found"),
    (r"no such (pane|agent)", "pane_lost"),
    (r"herdr-agent-worker:", "wrapper_error"),
    (r"HERDR_ENV", "wrapper_error"),
    (r"\btimed?[- ]?out\b", "timeout"),
    (r"Traceback \(most recent call last\)", "tool_error"),
    (r"\"error\"\s*:\s*\"", "tool_error"),
    (r"auto mode on", "wrapper_status_line"),
]
for pat, cat in sigs:
    if re.search(pat, text, re.I):
        print(cat)
        sys.exit(0)

print("unparseable_output")
'
}

# classify_worker_infra_error is deliberately narrow (sentinel-only, unlike
# the reviewer's broad heuristic): worker/resolver output is free-form agent
# chatter, so pattern-matching it for "looks like an error" would misfire on
# real work. Only the wrapper's own explicit signal is trusted here.
classify_worker_infra_error() { # stdin: worker/resolver agent output -> infra category (empty = not infra)
  python3 -c '
import re, sys
m = re.search(r"^KANBAN_INFRA_ERROR:\s*(.+)$", sys.stdin.read(), re.M)
if m:
    print(("wrapper_error: " + m.group(1).strip())[:200])
'
}

review_infra_backoff_seconds() { # review_infra_backoff_seconds <retry-n> -> short bounded backoff
  local n=$1 base=${DEFAULT_REVIEW_INFRA_BACKOFF_SECONDS:-2}
  local s=$((base * n))
  [[ $s -gt 10 ]] && s=10
  echo "$s"
}

record_review_infra_retry() { # record_review_infra_retry <card> <field> <heading> <category> <max> -> echoes new count
  local file=$1 field=$2 heading=$3 category=$4 max=$5 n
  n=$(($(fm_get "$file" "$field" 0) + 1))
  fm_set "$file" "$field" "$n"
  printf '%s retry %s/%s: %s\n' "$heading" "$n" "$max" "$category" | append_history "$file" "$heading retry"
  echo "$n"
}

review_prompt_for_card() { # review_prompt_for_card <card>
  local file=$1
  cat <<EOF
You are a strict reviewer. Inspect this repository's current state and judge
whether the task below is genuinely complete and of good quality. Check the
actual files and diffs; do not trust the worker's claims.

$(card_body "$file")

Output ONLY a JSON object: {"score": <0-100>, "feedback": "<what is missing or wrong, concretely>"}
EOF
}

invoke_reviewer() { # invoke_reviewer <card> <workdir> <prompt> <attempt-label> -> sets ATT_SCORE/ATT_FEEDBACK/ATT_REVIEW_INFRA_ERROR
  local file=$1 workdir=$2 prompt=$3 attempt_label=${4:-0}
  local id title rcmd review_out parsed t0
  ATT_REVIEW_SECS=${ATT_REVIEW_SECS:-0}
  id=$(fm_get "$file" id "?")
  title=$(fm_get "$file" title "")
  rcmd=$(review_cmd)
  t0=$SECONDS
  review_out=$( (cd "$workdir" && KANBAN_ACTIVITY_LOG=${KANBAN_ACTIVITY_LOG:-$KB/activity.jsonl} KANBAN_CARD_ID=$id KANBAN_CARD_ATTEMPT=$attempt_label KANBAN_CARD_TITLE=$title $rcmd 2>&1 <<<"$prompt") ) || true
  ATT_REVIEW_SECS=$((ATT_REVIEW_SECS + SECONDS - t0))
  echo "$review_out" | tail -n 40 | append_history "$file" "reviewer output (tail)"
  parsed=$(echo "$review_out" | parse_score)
  if [[ -z $parsed ]]; then
    ATT_SCORE=""
    ATT_FEEDBACK=""
    ATT_REVIEW_INFRA_ERROR=$(echo "$review_out" | classify_review_infra_error)
  else
    ATT_SCORE=${parsed%%$'\t'*}
    ATT_FEEDBACK=${parsed#*$'\t'}
    ATT_REVIEW_INFRA_ERROR=""
  fi
}

review_with_infra_retry() { # review_with_infra_retry <card> <workdir> <infra-max> <prompt> <attempt-label>
  # sets ATT_SCORE/ATT_FEEDBACK (only meaningful when ATT_REVIEW_INFRA_BLOCKED
  # is false) and ATT_REVIEW_INFRA_BLOCKED. Only a genuinely parsed JSON
  # score object -- from this call or an earlier retry within it -- is ever
  # threshold-judged; every infra failure in between is retried in place on
  # the same worktree/commit without touching `attempts`.
  local file=$1 workdir=$2 infra_max=$3 prompt=$4 attempt_label=${5:-0}
  ATT_REVIEW_INFRA_BLOCKED=false
  local retries
  retries=$(fm_get "$file" review_infra_retries 0)
  while true; do
    invoke_reviewer "$file" "$workdir" "$prompt" "$attempt_label"
    if [[ -z $ATT_REVIEW_INFRA_ERROR ]]; then
      fm_set "$file" review_infra_retries 0
      return
    fi
    if [[ $retries -ge $infra_max ]]; then
      ATT_REVIEW_INFRA_BLOCKED=true
      return
    fi
    retries=$(record_review_infra_retry "$file" review_infra_retries "review infrastructure" "$ATT_REVIEW_INFRA_ERROR" "$infra_max")
    sleep "$(review_infra_backoff_seconds "$retries")"
  done
}

run_attempt() { # run_attempt <card> <workdir> <worker-infra-max> -> sets ATT_SCORE / ATT_FEEDBACK / ATT_BLOCKED_REASON / ATT_WORKER_SECS / ATT_REVIEW_SECS
  # Runs ONLY the worker step. An agent-lifecycle failure in the worker
  # itself (visible Herdr pane lost before it ever touched the tree) is
  # retried here too, bounded and without consuming a worker attempt --
  # same principle as the reviewer side, applied symmetrically.
  local file=$1 workdir=$2 infra_max=${3:-$DEFAULT_REVIEW_INFRA_MAX_RETRIES}
  local id backend model wcmd out title infra_cat retries t0 attempt_label task_kind timebox_secs
  ATT_BLOCKED_REASON=""
  ATT_WORKER_STATUS=0
  ATT_WORKER_SECS=0
  ATT_REVIEW_SECS=0
  id=$(fm_get "$file" id "?")
  backend=$(fm_get "$file" backend "$DEFAULT_BACKEND")
  model=$(fm_get "$file" model "")
  title=$(fm_get "$file" title "")
  wcmd=$(worker_cmd "$backend" "$model")
  retries=$(fm_get "$file" worker_infra_retries 0)
  attempt_label=$(($(fm_get "$file" attempts 0) + 1))
  task_kind=$(fm_get "$file" task_kind implementation)
  timebox_secs=""
  if [[ $task_kind == diagnose ]]; then
    timebox_secs=$(($(fm_get "$file" diagnosis_max_minutes "$DEFAULT_DIAGNOSIS_MAX_MINUTES") * 60))
  fi
  while true; do
    # Custom worker commands (KANBAN_WORKER_CMD) receive the card's routing
    # via env, since the override bypasses worker_cmd's model handling.
    t0=$SECONDS
    ATT_WORKER_STATUS=0
    out=$( (cd "$workdir" && worker_prompt_for_card "$file" |
      KANBAN_CARD_ID=$id KANBAN_CARD_ATTEMPT=$attempt_label \
      KANBAN_ACTIVITY_LOG=${KANBAN_ACTIVITY_LOG:-$KB/activity.jsonl} \
      KANBAN_CARD_KIND=$task_kind KANBAN_CARD_TIMEBOX_SECS=$timebox_secs \
      KANBAN_CARD_MODEL=$model KANBAN_CARD_BACKEND=$backend KANBAN_CARD_TITLE=$title $wcmd 2>&1) ) || ATT_WORKER_STATUS=$?
    ATT_WORKER_SECS=$((ATT_WORKER_SECS + SECONDS - t0))
    echo "$out" | tail -n 40 | append_history "$file" "worker output (tail)"
    infra_cat=$(echo "$out" | classify_worker_infra_error)
    if [[ $infra_cat == *scope_timebox* ]]; then
      ATT_WORKER_INFRA_BLOCKED=false
      ATT_BLOCKED_REASON=" scope/timebox (hard maximum reached; partial evidence is in History if available)"
      ATT_SCORE=0
      ATT_FEEDBACK=""
      return
    fi
    if [[ -z $infra_cat ]]; then
      fm_set "$file" worker_infra_retries 0
      break
    fi
    if [[ $retries -ge $infra_max ]]; then
      ATT_WORKER_INFRA_BLOCKED=true
      return
    fi
    retries=$(record_review_infra_retry "$file" worker_infra_retries "worker infrastructure" "$infra_cat" "$infra_max")
    sleep "$(review_infra_backoff_seconds "$retries")"
  done
  ATT_WORKER_INFRA_BLOCKED=false

  # A worker that discovers a real-time ordering dependency (e.g. it needs
  # another card's result that is not merged yet) signals it instead of
  # guessing; the dialogue secretary is never consulted for this. See
  # detect_blocked().
  detect_blocked "$out"
  ATT_BLOCKED_REASON=$BLOCKED_REASON
  if [[ -n $ATT_BLOCKED_REASON ]]; then
    ATT_SCORE=0
    ATT_FEEDBACK=""
  fi
}

record_attempt() { # record_attempt <card> <threshold> -> increments attempts
  local file=$1 threshold=$2 attempts timings
  attempts=$(($(fm_get "$file" attempts 0) + 1))
  fm_set "$file" attempts "$attempts"
  timings="worker=${ATT_WORKER_SECS}s review=${ATT_REVIEW_SECS}s"
  fm_set "$file" last_timings "$timings"
  printf 'score: %s / threshold: %s\nphase durations: %s\n\n%s\n' "$ATT_SCORE" "$threshold" "$timings" "$ATT_FEEDBACK" |
    append_history "$file" "review"
}

notify_result() { # notify_result <done|failed> <title> ; optional hook, never fatal
  if [[ -n ${KANBAN_NOTIFY_CMD:-} ]]; then
    local out
    if ! out=$($KANBAN_NOTIFY_CMD "$1" "$2" 2>&1); then
      echo "kanban: notification hook failed for state=$1 title=$2: ${out:-no detail}" >&2
    fi
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

review_prompt_for_resolve() { # review_prompt_for_resolve <card> <card_branch> <base_branch>
  local file=$1 card_branch=$2 base_branch=$3
  cat <<EOF
You are a strict reviewer. This worktree is the result of resolving a merge
conflict between card branch $card_branch and base branch $base_branch.
Inspect the actual files and diffs; do not trust the resolver's claims. Judge
whether BOTH sides' intent was preserved and the task below is genuinely
complete.

$(card_body "$file")

Output ONLY a JSON object: {"score": <0-100>, "feedback": "<what is missing or wrong, concretely>"}
EOF
}

run_resolve_attempt() { # run_resolve_attempt <card> <resolve-workdir> <conflict-files> <base-branch> <card-branch> -> sets ATT_UNRESOLVED/ATT_RESOLVE_SECS
  # Runs ONLY the resolver step (agent + commit); sets ATT_UNRESOLVED=true
  # when conflict markers remain. Review is a separate step -- see
  # review_with_infra_retry -- so an infra failure there never re-runs the
  # resolver.
  local file=$1 workdir=$2 conflict_files=$3 base_branch=$4 card_branch=$5
  local id backend model wcmd out title prompt t0 attempt_label
  ATT_RESOLVE_SECS=0
  ATT_REVIEW_SECS=0
  id=$(fm_get "$file" id "?")
  backend=$(fm_get "$file" backend "$DEFAULT_BACKEND")
  model=$(fm_get "$file" model "")
  title=$(fm_get "$file" title "")
  wcmd=$(resolve_cmd "$backend" "$model")
  attempt_label="resolve-$(($(fm_get "$file" resolve_attempts 0) + 1))"
  prompt=$(printf 'You are the conflict-resolution role for MornKanban. Card branch %s passed review but conflicts with the current base branch %s. Resolve the conflict in this worktree, preserving the intent of BOTH sides -- never simply discard one side. Run any tests the task requires, then leave the tree conflict-free.\n\nConflicted files:\n%s\n\nOriginal task:\n%s\n' \
    "$card_branch" "$base_branch" "$conflict_files" "$(card_body "$file")")
  t0=$SECONDS
  out=$( (cd "$workdir" && printf '%s' "$prompt" |
    KANBAN_CARD_ID=$id KANBAN_CARD_ATTEMPT=$attempt_label \
    KANBAN_ACTIVITY_LOG=${KANBAN_ACTIVITY_LOG:-$KB/activity.jsonl} \
    KANBAN_CARD_MODEL=$model KANBAN_CARD_BACKEND=$backend KANBAN_CARD_TITLE=$title \
    KANBAN_CONFLICT_FILES=$conflict_files KANBAN_BASE_BRANCH=$base_branch KANBAN_CARD_BRANCH=$card_branch \
    $wcmd 2>&1) ) || true
  ATT_RESOLVE_SECS=$((SECONDS - t0))
  echo "$out" | tail -n 40 | append_history "$file" "resolver output (tail)"
  git -C "$workdir" add -A
  git -C "$workdir" commit -q --allow-empty -m "kanban: resolve conflict for $title"

  ATT_UNRESOLVED=false
  if git -C "$workdir" diff --name-only --diff-filter=U | grep -q .; then
    ATT_UNRESOLVED=true
  fi
}

record_resolve_attempt() { # record_resolve_attempt <card> <threshold> -> increments resolve_attempts
  local file=$1 threshold=$2 attempts timings
  attempts=$(($(fm_get "$file" resolve_attempts 0) + 1))
  fm_set "$file" resolve_attempts "$attempts"
  timings="resolver=${ATT_RESOLVE_SECS}s review=${ATT_REVIEW_SECS}s"
  fm_set "$file" last_timings "$timings"
  printf 'score: %s / threshold: %s\nphase durations: %s\n\n%s\n' "$ATT_SCORE" "$threshold" "$timings" "$ATT_FEEDBACK" |
    append_history "$file" "resolve review"
}

process_resolve_wt() { # process_resolve_wt <card> <base_branch> <card_branch> <card_wt> <conflict_files>
  # Called when a card passed review but its branch conflicts with base at
  # merge time. Dedicated resolver role: never discards either side, keeps
  # both branches until it truly succeeds or gives up, and only ever merges
  # the resolve branch into base (never the original card branch again --
  # no double-merge).
  local file=$1 base_branch=$2 card_branch=$3 card_wt=$4 conflict_files=$5
  local id title threshold resolve_max_attempts resolve_attempts review_infra_max review_enabled review_source
  id=$(fm_get "$file" id "?")
  title=$(fm_get "$file" title "?")
  threshold=$(fm_get "$file" threshold "$DEFAULT_THRESHOLD")
  resolve_max_attempts=$(fm_get "$file" resolve_max_attempts "$DEFAULT_RESOLVE_MAX_ATTEMPTS")
  resolve_attempts=$(fm_get "$file" resolve_attempts 0)
  review_infra_max=$(fm_get "$file" review_infra_max_retries "$DEFAULT_REVIEW_INFRA_MAX_RETRIES")
  local resolve_branch=kanban-resolve/$id resolve_wt=$KB/wt/$id-resolve
  local tag="[$title]"
  resolve_card_review "$file"
  review_enabled=$CARD_REVIEW_ENABLED
  review_source=$CARD_REVIEW_SOURCE

  [[ $file == "$KB/resolving/"* ]] || move_card "$file" resolving >/dev/null
  file=$KB/resolving/$(basename "$file")
  [[ -n $card_wt ]] && git -C "$ROOT" worktree remove --force "$card_wt" 2>/dev/null || true

  # Resuming a review-infra-blocked resolve card (see cmd_resume): the
  # resolve worktree/branch already survived the block, reuse them instead
  # of failing on `worktree add`'s "branch already exists".
  if git -C "$ROOT" show-ref --verify --quiet "refs/heads/$resolve_branch" && [[ -d $resolve_wt ]]; then
    echo "$tag resuming existing resolve worktree/branch"
  elif ! git -C "$ROOT" worktree add -q -b "$resolve_branch" "$resolve_wt" "$base_branch" 2>>"$KB/wt/$id.log"; then
    echo "resolve worktree add failed; see .kanban/wt/$id.log" | append_history "$file" "error"
    git -C "$ROOT" branch -q -D "$card_branch" 2>/dev/null || true
    move_card "$file" failed >/dev/null
    echo "$tag FAIL resolve worktree add failed -> failed"
    notify_result failed "$title"
    return
  else
    git -C "$resolve_wt" merge --no-ff -q -m "kanban: merge $card_branch for conflict resolution" "$card_branch" \
      2>>"$KB/wt/$id.log" || true
  fi

  local resolved=false blocked_infra=false
  while [[ $resolve_attempts -lt $resolve_max_attempts ]]; do
    echo "$tag resolve attempt $((resolve_attempts + 1))/$resolve_max_attempts (branch: $resolve_branch)"
    run_resolve_attempt "$file" "$resolve_wt" "$conflict_files" "$base_branch" "$card_branch"
    if $ATT_UNRESOLVED; then
      resolve_attempts=$((resolve_attempts + 1))
      fm_set "$file" resolve_attempts "$resolve_attempts"
      printf 'conflict markers remain unresolved after the resolver attempt.\n' | append_history "$file" "resolve review"
      echo "$tag RESOLVE RETRY conflict markers remain"
      continue
    fi
    if [[ $review_enabled != true ]]; then
      resolve_attempts=$((resolve_attempts + 1))
      fm_set "$file" resolve_attempts "$resolve_attempts"
      echo "review skipped: review_enabled=false (source: $review_source)" | append_history "$file" "resolve review"
      resolved=true
      break
    fi
    review_with_infra_retry "$file" "$resolve_wt" "$review_infra_max" \
      "$(review_prompt_for_resolve "$file" "$card_branch" "$base_branch")" \
      "resolve-$((resolve_attempts + 1))"
    if $ATT_REVIEW_INFRA_BLOCKED; then
      blocked_infra=true
      break
    fi
    record_resolve_attempt "$file" "$threshold"
    resolve_attempts=$((resolve_attempts + 1))
    if [[ $ATT_SCORE -ge $threshold ]]; then resolved=true; break; fi
    echo "$tag RESOLVE RETRY score=$ATT_SCORE"
    printf '%s\n' "$ATT_FEEDBACK" | append_history "$file" "rework instruction (fix these points)"
  done

  if $blocked_infra; then
    fm_set "$file" blocked_kind review_infra
    printf 'review infrastructure retries exhausted (%s/%s) for resolve branch %s.\nbranches %s and %s, and the resolve worktree, are kept -- this is a review infrastructure stop, not a code failure.\nrecovery: kanban resume %s\n' \
      "$(fm_get "$file" review_infra_retries 0)" "$review_infra_max" "$resolve_branch" "$resolve_branch" "$card_branch" "$id" |
      append_history "$file" "blocked (review infrastructure)"
    move_card "$file" blocked >/dev/null
    echo "$tag BLOCKED review-infra retries exhausted -> blocked (branches $resolve_branch, $card_branch kept; not a code failure)"
    notify_result blocked "$title"
    return
  fi

  if ! $resolved; then
    printf 'conflict files: %s\nresolve branch %s and original card branch %s are kept for manual inspection.\n' \
      "$conflict_files" "$resolve_branch" "$card_branch" | append_history "$file" "gave up (conflict unresolved)"
    git -C "$ROOT" worktree remove --force "$resolve_wt" 2>/dev/null || true
    move_card "$file" failed >/dev/null
    if [[ $review_enabled == true ]]; then
      echo "$tag FAIL resolve score=$ATT_SCORE attempts exhausted -> failed (branches $resolve_branch, $card_branch kept)"
    else
      echo "$tag FAIL unresolved conflict (review disabled) -> failed (branches $resolve_branch, $card_branch kept)"
    fi
    notify_result failed "$title"
    return
  fi

  local merge_t0=$SECONDS merge_secs
  merge_lock acquire
  if git -C "$ROOT" merge --no-ff -q -m "kanban: $title (conflict resolved)" "$resolve_branch" 2>>"$KB/wt/$id.log"; then
    merge_lock release
    merge_secs=$((SECONDS - merge_t0))
    echo "phase durations: merge=${merge_secs}s" | append_history "$file" "merged"
    git -C "$ROOT" worktree remove --force "$resolve_wt" 2>/dev/null || true
    git -C "$ROOT" branch -q -D "$resolve_branch" "$card_branch" 2>/dev/null || true
    rm -f "$KB/wt/$id.log"
    move_card "$file" done >/dev/null
    if [[ $review_enabled == true ]]; then
      echo "$tag PASS resolve score=$ATT_SCORE -> done (merged into $base_branch)"
    else
      echo "$tag PASS resolve (review disabled) -> done (merged into $base_branch)"
    fi
    notify_result done "$title"
  else
    git -C "$ROOT" merge --abort 2>/dev/null || true
    merge_lock release
    git -C "$ROOT" worktree remove --force "$resolve_wt" 2>/dev/null || true
    local resolve_note
    if [[ $review_enabled == true ]]; then resolve_note="resolve passed review (score $ATT_SCORE)"; else resolve_note="resolve completed (review disabled)"; fi
    printf '%s but merging %s into %s failed; branches %s and %s kept for manual merge.\n' \
      "$resolve_note" "$resolve_branch" "$base_branch" "$resolve_branch" "$card_branch" |
      append_history "$file" "merge conflict (post-resolve)"
    move_card "$file" failed >/dev/null
    echo "$tag CONFLICT (post-resolve) -> failed (branches kept; merge manually)"
    notify_result failed "$title"
  fi
}

process_card_seq() { # non-git fallback: run in place, retry via todo
  local file=$1
  local title threshold max_attempts attempts review_infra_max review_enabled review_source
  title=$(fm_get "$file" title "?")
  threshold=$(fm_get "$file" threshold "$DEFAULT_THRESHOLD")
  max_attempts=$(fm_get "$file" max_attempts "$DEFAULT_MAX_ATTEMPTS")
  attempts=$(fm_get "$file" attempts 0)
  review_infra_max=$(fm_get "$file" review_infra_max_retries "$DEFAULT_REVIEW_INFRA_MAX_RETRIES")
  resolve_card_review "$file"
  review_enabled=$CARD_REVIEW_ENABLED
  review_source=$CARD_REVIEW_SOURCE

  echo "==> [$title] attempt $((attempts + 1))/$max_attempts"
  run_attempt "$file" "$ROOT" "$review_infra_max"
  if $ATT_WORKER_INFRA_BLOCKED; then
    fm_set "$file" blocked_kind review_infra
    printf 'worker infrastructure retries exhausted (%s/%s); this is not a code failure.\nrecovery: kanban resume %s\n' \
      "$(fm_get "$file" worker_infra_retries 0)" "$review_infra_max" "$(fm_get "$file" id "?")" |
      append_history "$file" "blocked (worker infrastructure)"
    move_card "$file" blocked >/dev/null
    echo "    BLOCKED worker-infra retries exhausted -> blocked (not a code failure)"
    notify_result blocked "$title"
    return
  fi
  if [[ -n $ATT_BLOCKED_REASON ]]; then
    printf 'worker reported a real-time ordering dependency:%s\n' "$ATT_BLOCKED_REASON" |
      append_history "$file" "blocked"
    move_card "$file" blocked >/dev/null
    echo "    BLOCKED ->$ATT_BLOCKED_REASON -> blocked (reclaimed on next dispatcher pass)"
    return
  fi

  if [[ $review_enabled != true ]]; then
    attempts=$((attempts + 1))
    fm_set "$file" attempts "$attempts"
    if [[ $ATT_WORKER_STATUS -eq 0 ]]; then
      echo "review skipped: review_enabled=false (source: $review_source)" | append_history "$file" "review"
      move_card "$file" done >/dev/null
      echo "    PASS (review disabled) -> done"
      notify_result done "$title"
    else
      echo "worker exited with status $ATT_WORKER_STATUS" | append_history "$file" "worker failure"
      move_card "$file" failed >/dev/null
      echo "    FAIL worker exit=$ATT_WORKER_STATUS (review disabled) -> failed (needs human)"
      notify_result failed "$title"
    fi
    return
  fi

  review_with_infra_retry "$file" "$ROOT" "$review_infra_max" "$(review_prompt_for_card "$file")" "$((attempts + 1))"
  if $ATT_REVIEW_INFRA_BLOCKED; then
    fm_set "$file" blocked_kind review_infra
    printf 'review infrastructure retries exhausted (%s/%s); this is not a code failure.\nrecovery: kanban resume %s\n' \
      "$(fm_get "$file" review_infra_retries 0)" "$review_infra_max" "$(fm_get "$file" id "?")" |
      append_history "$file" "blocked (review infrastructure)"
    move_card "$file" blocked >/dev/null
    echo "    BLOCKED review-infra retries exhausted -> blocked (not a code failure)"
    notify_result blocked "$title"
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
  local id title threshold max_attempts attempts review_infra_max review_enabled review_source task_kind
  id=$(fm_get "$file" id "?")
  title=$(fm_get "$file" title "?")
  threshold=$(fm_get "$file" threshold "$DEFAULT_THRESHOLD")
  max_attempts=$(fm_get "$file" max_attempts "$DEFAULT_MAX_ATTEMPTS")
  attempts=$(fm_get "$file" attempts 0)
  review_infra_max=$(fm_get "$file" review_infra_max_retries "$DEFAULT_REVIEW_INFRA_MAX_RETRIES")
  task_kind=$(fm_get "$file" task_kind implementation)
  local branch=kanban/$id wt=$KB/wt/$id
  local tag="[$title]"
  resolve_card_review "$file"
  review_enabled=$CARD_REVIEW_ENABLED
  review_source=$CARD_REVIEW_SOURCE

  # A dispatcher restart reclaims a stranded `doing` card by moving it back
  # to todo without touching its worktree/branch (see cmd_run). If both
  # already exist here, this is that resume -- reuse them instead of
  # failing on `git worktree add`'s "branch already exists" error, and skip
  # re-running the worker below when review_pending shows only the review
  # step was interrupted.
  if git -C "$ROOT" show-ref --verify --quiet "refs/heads/$branch" && [[ -d $wt ]]; then
    echo "$tag resuming existing worktree/branch after restart"
  elif ! git -C "$ROOT" worktree add -q -b "$branch" "$wt" "$base_branch" 2>>"$KB/wt/$id.log"; then
    echo "worktree add failed; see .kanban/wt/$id.log" | append_history "$file" "error"
    move_card "$file" failed >/dev/null
    echo "$tag FAIL worktree add failed -> failed"
    notify_result failed "$title"
    return
  fi

  local passed=false blocked_infra=false
  while [[ $attempts -lt $max_attempts ]]; do
    if [[ $(fm_get "$file" review_pending "") == 1 ]]; then
      echo "$tag resuming pending review (branch: $branch)"
      ATT_WORKER_SECS=0
      ATT_REVIEW_SECS=0
    else
      echo "$tag attempt $((attempts + 1))/$max_attempts (branch: $branch)"
      run_attempt "$file" "$wt" "$review_infra_max"
      if $ATT_WORKER_INFRA_BLOCKED; then
        blocked_infra=true
        break
      fi
      if [[ -n $ATT_BLOCKED_REASON ]]; then
        printf 'worker reported a real-time ordering dependency:%s\nworktree is discarded; the card restarts on a fresh worktree from the next pickup.\n' \
          "$ATT_BLOCKED_REASON" | append_history "$file" "blocked"
        git -C "$ROOT" worktree remove --force "$wt" 2>/dev/null || true
        git -C "$ROOT" branch -q -D "$branch" 2>/dev/null || true
        move_card "$file" blocked >/dev/null
        echo "$tag BLOCKED ->$ATT_BLOCKED_REASON -> blocked"
        return
      fi
      if [[ $task_kind == diagnose ]]; then
        local diagnosis_changes
        diagnosis_changes=$(git -C "$wt" status --porcelain --untracked-files=all)
        if [[ -n $diagnosis_changes ]]; then
          printf 'diagnose card violated its read-only contract; changes were discarded and never merged:\n%s\n' \
            "$diagnosis_changes" | append_history "$file" "diagnosis read-only violation"
          git -C "$wt" reset --hard -q
          git -C "$wt" clean -fdq
          ATT_WORKER_STATUS=65
        fi
      fi
      git -C "$wt" add -A
      git -C "$wt" commit -q --allow-empty -m "kanban: $title (attempt $((attempts + 1)))"
      if [[ $review_enabled != true ]]; then
        attempts=$((attempts + 1))
        fm_set "$file" attempts "$attempts"
        if [[ $ATT_WORKER_STATUS -eq 0 ]]; then
          passed=true
          echo "review skipped: review_enabled=false (source: $review_source)" | append_history "$file" "review"
        else
          echo "worker exited with status $ATT_WORKER_STATUS" | append_history "$file" "worker failure"
        fi
        break
      fi
      fm_set "$file" review_pending 1
    fi

    review_with_infra_retry "$file" "$wt" "$review_infra_max" "$(review_prompt_for_card "$file")" "$((attempts + 1))"
    if $ATT_REVIEW_INFRA_BLOCKED; then
      blocked_infra=true
      break
    fi
    fm_set "$file" review_pending ""
    record_attempt "$file" "$threshold"
    attempts=$((attempts + 1))
    if [[ $ATT_SCORE -ge $threshold ]]; then passed=true; break; fi
    echo "$tag RETRY score=$ATT_SCORE"
    printf '%s\n' "$ATT_FEEDBACK" | append_history "$file" "rework instruction (fix these points)"
  done

  if $blocked_infra; then
    fm_set "$file" blocked_kind review_infra
    printf 'agent infrastructure retries exhausted (worker: %s/%s, review: %s/%s) for branch %s.\nbranch, worktree, and every commit so far are kept -- this is a review infrastructure stop, not a code failure.\nrecovery: kanban resume %s\n' \
      "$(fm_get "$file" worker_infra_retries 0)" "$review_infra_max" \
      "$(fm_get "$file" review_infra_retries 0)" "$review_infra_max" "$branch" "$id" |
      append_history "$file" "blocked (review infrastructure)"
    move_card "$file" blocked >/dev/null
    echo "$tag BLOCKED agent-infra retries exhausted -> blocked (branch $branch kept; not a code failure)"
    notify_result blocked "$title"
    return
  fi

  if ! $passed; then
    printf 'branch %s is kept for manual inspection.\n' "$branch" | append_history "$file" "gave up"
    git -C "$ROOT" worktree remove --force "$wt" 2>/dev/null || true
    move_card "$file" failed >/dev/null
    if [[ $review_enabled == true ]]; then
      echo "$tag FAIL score=$ATT_SCORE attempts exhausted -> failed (branch $branch kept)"
    else
      echo "$tag FAIL worker exit=$ATT_WORKER_STATUS (review disabled) -> failed (branch $branch kept)"
    fi
    notify_result failed "$title"
    return
  fi

  local merge_t0=$SECONDS merge_secs
  merge_lock acquire
  if git -C "$ROOT" merge --no-ff -q -m "kanban: $title" "$branch" 2>>"$KB/wt/$id.log"; then
    merge_lock release
    merge_secs=$((SECONDS - merge_t0))
    echo "phase durations: merge=${merge_secs}s" | append_history "$file" "merged"
    git -C "$ROOT" worktree remove --force "$wt" 2>/dev/null || true
    git -C "$ROOT" branch -q -D "$branch" 2>/dev/null || true
    rm -f "$KB/wt/$id.log"
    move_card "$file" done >/dev/null
    if [[ $review_enabled == true ]]; then
      echo "$tag PASS score=$ATT_SCORE -> done (merged into $base_branch)"
    else
      echo "$tag PASS (review disabled) -> done (merged into $base_branch)"
    fi
    notify_result done "$title"
  else
    local conflict_files
    conflict_files=$(git -C "$ROOT" diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ' ')
    git -C "$ROOT" merge --abort 2>/dev/null || true
    merge_lock release
    local pass_note
    if [[ $review_enabled == true ]]; then pass_note="work passed review (score $ATT_SCORE)"; else pass_note="work completed (review disabled)"; fi
    printf '%s but merging %s into %s conflicted on: %s\nhanding off to the resolver role instead of failing immediately.\n' \
      "$pass_note" "$branch" "$base_branch" "$conflict_files" | append_history "$file" "merge conflict"
    process_resolve_wt "$file" "$base_branch" "$branch" "$wt" "$conflict_files"
  fi
}

cmd_run() {
  # Capture a real environment override before require_root loads project
  # defaults into KANBAN_JOBS. Environment and -j intentionally pin the
  # dispatcher; otherwise KANBAN.md's jobs value is re-read while running.
  local jobs_env=${KANBAN_JOBS:-}
  require_root
  local once=false jobs_pinned=false jobs_max
  local dispatch_poll_interval=${KANBAN_DISPATCH_POLL_INTERVAL:-1}
  if [[ -n $jobs_env ]]; then
    jobs_max=$jobs_env
    jobs_pinned=true
  else
    jobs_max=$(fm_get "$KB/KANBAN.md" jobs 1)
  fi
  while [[ $# -gt 0 ]]; do
    case $1 in
      --once) once=true; shift ;;
      -j|--jobs) jobs_max=$2; jobs_pinned=true; shift 2 ;;
      *) die "usage: kanban run [--once] [-j N]" ;;
    esac
  done
  [[ $jobs_max =~ ^[1-9][0-9]*$ ]] || die "jobs must be a positive integer (got: $jobs_max)"
  python3 -c 'import sys; assert float(sys.argv[1]) > 0' "$dispatch_poll_interval" 2>/dev/null ||
    die "KANBAN_DISPATCH_POLL_INTERVAL must be a positive number"
  local lock=$KB/.lock
  if [[ -f $lock ]] && kill -0 "$(cat "$lock")" 2>/dev/null; then
    die "dispatcher already running (pid $(cat "$lock"))"
  fi
  echo $$ >"$lock"
  trap "rm -f '$lock'; rmdir '$KB/.merge.lock' 2>/dev/null || true" EXIT
  # Fail fast if no backend CLI is available (dies with a message here instead
  # of silently inside a background job).
  worker_cmd "$DEFAULT_BACKEND" "" >/dev/null
  local project_default_review=true
  if [[ -n ${KANBAN_REVIEW_ENABLED:-} ]]; then
    project_default_review=$KANBAN_REVIEW_ENABLED
  elif [[ -n $PROJECT_REVIEW_ENABLED ]]; then
    project_default_review=$PROJECT_REVIEW_ENABLED
  fi
  if [[ $project_default_review == true ]]; then review_cmd >/dev/null; fi
  if [[ $project_default_review == true ]]; then
    echo "kanban: Review: ON (project default; cards may opt out with --no-review)"
  else
    echo "kanban: Review: OFF (fast iteration; project default; cards may opt in with --review)"
  fi
  if $jobs_pinned; then
    echo "kanban: Jobs: $jobs_max (pinned by -j or KANBAN_JOBS)"
  else
    echo "kanban: Jobs: $jobs_max (live from .kanban/KANBAN.md; edit jobs: to resize safely)"
  fi
  echo "[UNRESTRICTED] worker/reviewer permission policy: claude=$(claude_perm_flag) codex=$(codex_sandbox_flag)" >&2
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
      # review-infra blocked cards are a deliberate, terminal stop: their
      # worktree/branch/commits are the whole point of keeping them, and
      # they wait for an explicit `kanban resume <id>` rather than being
      # silently requeued (which would either re-run the worker for no
      # reason, or collide with the surviving worktree/branch). Only the
      # older "worker-reported ordering dependency" blocked kind is
      # auto-reclaimed to todo on every restart, same as before.
      if [[ $(fm_get "$orphan" blocked_kind "") == review_infra ]]; then
        continue
      fi
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
    if ! $jobs_pinned; then
      local configured_jobs
      configured_jobs=$(fm_get "$KB/KANBAN.md" jobs "$jobs_max")
      if [[ $configured_jobs =~ ^[1-9][0-9]*$ ]]; then
        if [[ $configured_jobs -ne $jobs_max ]]; then
          echo "kanban: Jobs resized $jobs_max -> $configured_jobs (running jobs are kept)"
          jobs_max=$configured_jobs
        fi
      else
        echo "kanban: ignoring invalid live jobs value '$configured_jobs'; keeping $jobs_max" >&2
      fi
    fi
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
    sleep "$dispatch_poll_interval"
  done
  wait
  echo "todo is empty"
}

cmd_resume() { # cmd_resume <id-substring> -> re-run only the review step of a review-infra-blocked card
  require_root
  local pat=${1:?usage: kanban resume <id>}
  local hits=("$KB"/blocked/*"$pat"*.md)
  [[ -e ${hits[0]} ]] || die "no blocked card matching '$pat'"
  [[ ${#hits[@]} -eq 1 ]] || die "'$pat' matches multiple blocked cards; use the full id"
  local file=${hits[0]} kind
  kind=$(fm_get "$file" blocked_kind "")
  [[ $kind == review_infra ]] || die "card is blocked (kind: ${kind:-unknown}); only review_infra blocks can be resumed"

  fm_set "$file" review_infra_retries 0
  fm_set "$file" worker_infra_retries 0
  fm_set "$file" blocked_kind ""
  local id
  id=$(fm_get "$file" id "?")
  local picked
  picked=$(move_card "$file" doing)

  if ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    process_card_seq "$picked"
    return
  fi
  local base_branch
  base_branch=$(git -C "$ROOT" symbolic-ref --short HEAD) || die "detached HEAD; check out a branch first"
  if git -C "$ROOT" show-ref --verify --quiet "refs/heads/kanban-resolve/$id"; then
    # was blocked mid conflict-resolve review: resume via the resolve
    # worktree/branch, which process_resolve_wt now reuses instead of
    # re-adding (see the resume check right before its worktree add).
    process_resolve_wt "$picked" "$base_branch" "kanban/$id" "" ""
    return
  fi
  process_card_wt "$picked" "$base_branch"
}

case ${1:-} in
  init) shift; cmd_init "$@" ;;
  add) shift; cmd_add "$@" ;;
  list|ls) cmd_list ;;
  show) shift; cmd_show "${1:?usage: kanban show <id>}" ;;
  run) shift || true; cmd_run "$@" ;;
  resume) shift; cmd_resume "$@" ;;
  monitor) shift || true; cmd_monitor "$@" ;;
  projects) shift; python3 "$REGISTRY_CLI" projects "$@" ;;
  send) shift; python3 "$REGISTRY_CLI" send "$@" ;;
  --version) cat "$VERSION_FILE" ;;
  version) cmd_version ;;
  install) cmd_install ;;
  update) cmd_update ;;
  uninstall) cmd_uninstall ;;
  *) die "usage: kanban {init|add|list|show|run [--once] [-j N]|resume <id>|monitor [run|daemon|config]|projects {add|list|show|update|remove}|send <alias> \"title\"|install|update|uninstall|version|--version}" ;;
esac
