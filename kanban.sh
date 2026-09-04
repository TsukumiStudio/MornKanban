#!/usr/bin/env bash
# kanban.sh - file-based kanban dispatcher for agent workers.
# Cards live in <project>/.git/kanban/{backlog,todo,doing,review,resolving,blocked,done,failed}/ as Markdown
# with YAML frontmatter. `kanban run` executes cards via a worker backend
# (claude / codex, or a visible Herdr wrapper), then scores the result with a
# review agent and loops until the score passes the threshold or attempts run out.
# In a git repository each card runs in its own worktree on branch
# kanban/<id>, so `kanban run -j N` processes N cards in parallel; passing
# work is merged back into the base branch (merges are serialized).
# See README.md for the workflow contract.
set -euo pipefail

STATES=(backlog todo doing review resolving blocked done failed)
DEFAULT_THRESHOLD=80
DEFAULT_MAX_ATTEMPTS=3
DEFAULT_RESOLVE_MAX_ATTEMPTS=2
DEFAULT_BACKEND=auto
DEFAULT_MODEL=""
DEFAULT_REVIEW_MODEL="haiku"
DEFAULT_JOBS=4
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
# shellcheck source=kanban-root.sh
source "$SELF_DIR/kanban-root.sh"

cmd_version() { python3 "$SETUP_CLI" version; }
cmd_install() { python3 "$SETUP_CLI" install; }
cmd_update() { python3 "$SETUP_CLI" update; }
cmd_uninstall() { python3 "$SETUP_CLI" uninstall; }

find_root() {
  kanban_project_root "$PWD"
}

require_root() {
  ROOT=$(find_root) || die "Git repository required"
  KB=$(kanban_board_dir "$ROOT") || die "could not resolve the Git common directory"
  [[ -d $KB ]] || die "no board at $KB (run: kanban init)"
  load_project_config
}

cfg_env() { # cfg_env <file> <key> <env-name>: env wins; else adopt non-empty cfg value
  local v
  v=$(fm_get "$1" "$2" "")
  if [[ -n $v ]] && ! eval "[[ -n \${$3:-} ]]"; then
    eval "export $3=\"\$v\""
  fi
}

load_project_config() { # .git/kanban/KANBAN.md frontmatter -> defaults (env still wins)
  local cfg=$KB/KANBAN.md
  if [[ ! -f $cfg ]]; then
    [[ -n ${KANBAN_REVIEW_INFRA_MAX_RETRIES:-} ]] && DEFAULT_REVIEW_INFRA_MAX_RETRIES=$KANBAN_REVIEW_INFRA_MAX_RETRIES
    [[ -n ${KANBAN_REVIEW_INFRA_BACKOFF_SECONDS:-} ]] && DEFAULT_REVIEW_INFRA_BACKOFF_SECONDS=$KANBAN_REVIEW_INFRA_BACKOFF_SECONDS
    [[ -z ${KANBAN_REVIEW_MODEL:-} ]] && export KANBAN_REVIEW_MODEL=$DEFAULT_REVIEW_MODEL
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
  [[ -z ${KANBAN_REVIEW_MODEL:-} ]] && export KANBAN_REVIEW_MODEL=$DEFAULT_REVIEW_MODEL
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

validate_effort() { # validate_effort <value>; empty inherits the agent's shared setting
  case ${1:-} in
    ""|low|medium|high|xhigh|max) ;;
    *) die "invalid effort: '$1' (low|medium|high|xhigh|max)" ;;
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

fm_update() { # fm_update <file> <key> <value> [<key> <value> ...]
  python3 - "$@" <<'EOF'
import os, stat, sys, tempfile
path, raw = sys.argv[1], sys.argv[2:]
if len(raw) % 2:
    raise SystemExit("frontmatter updates require key/value pairs")
updates = dict(zip(raw[::2], raw[1::2]))
lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
out, in_fm, seen = [], False, 0
for line in lines:
    if line == "---" and seen < 2:
        seen += 1
        in_fm = seen == 1
        if seen == 2:
            for key, value in updates.items():
                out.append(f"{key}: {value}")
            updates.clear()
    elif in_fm:
        key = line.partition(":")[0]
        if key in updates:
            line = f"{key}: {updates.pop(key)}"
    out.append(line)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".card.", suffix=".tmp")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp):
        os.remove(tmp)
EOF
}

fm_set() { # fm_set <file> <key> <value>
  fm_update "$@"
}

card_task() { # only the stable task specification, never accumulated History
  awk '/^## (Goal|Task)$/{task=1} /^## History$/{if(task) exit} task{print}' "$1"
}

section_body() { # section_body <card> <heading>
  awk -v heading="$2" '
$0 == "## " heading {inside=1; next}
inside && /^## / {exit}
inside {print}
' "$1"
}

card_ready_errors() { # card_ready_errors <card>; prints missing Definition-of-Ready fields
  local file=$1 missing=0
  if [[ $(fm_get "$file" card_schema legacy) != structured ]]; then return 0; fi
  if [[ -z $(fm_get "$file" type "") ]]; then echo "missing: type"; missing=1; fi
  if [[ -z $(fm_get "$file" size "") ]]; then echo "missing: size"; missing=1; fi
  if [[ -z $(section_body "$file" Goal | awk 'NF{print; exit}') ]]; then echo "missing: Goal"; missing=1; fi
  if [[ -z $(section_body "$file" "Acceptance Criteria" | awk '$0 ~ /^- / && length($0)>2{print; exit}') ]]; then
    echo "missing: Acceptance Criteria"; missing=1
  fi
  if [[ -z $(section_body "$file" Scope | awk 'NF{print; exit}') ]]; then echo "missing: Scope"; missing=1; fi
  return "$missing"
}

latest_rework_feedback() { # body of the newest rework-instruction History entry
  awk '
/^### [0-9][0-9][0-9][0-9]-.* rework instruction/ {capture=1; feedback=""; next}
/^### [0-9][0-9][0-9][0-9]-/ {capture=0}
capture {feedback=feedback $0 ORS}
END {printf "%s", feedback}
' "$1"
}

latest_user_decision() { # body of the newest user-decision History entry
  awk '
/^### [0-9][0-9][0-9][0-9]-.* user decision/ {capture=1; decision=""; next}
/^### [0-9][0-9][0-9][0-9]-/ {capture=0}
capture {decision=decision $0 ORS}
END {printf "%s", decision}
' "$1"
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

write_artifact() { # write_artifact <path>; content from stdin, atomically
  local dest=$1 dir tmp
  dir=$(dirname "$dest")
  mkdir -p "$dir"
  tmp=$(mktemp "$dir/.artifact.XXXXXX")
  chmod 644 "$tmp"
  cat >"$tmp"
  mv "$tmp" "$dest"
}

artifact_path() { # artifact_path <briefs|reports|reviews> <card> <attempt-label>
  printf '%s/%s/%s-r%s.md\n' "$KB" "$1" "$(fm_get "$2" id unknown)" "$3"
}

report_missing_sections() { # report_missing_sections <report>; empty means valid
  local heading missing=""
  for heading in "Summary" "Acceptance Criteria & Evidence" "Verification" "Changes" "Deviations & Decisions" "Follow-ups"; do
    if [[ -z $(section_body "$1" "$heading" | awk 'NF{print; exit}') ]]; then
      missing="${missing}${missing:+, }$heading"
    fi
  done
  printf '%s' "$missing"
}

move_card() { # move_card <file> <state> -> echoes new path
  local dest=$KB/$2/$(basename "$1")
  mv "$1" "$dest"
  echo "$dest"
}

card_state_by_id() { # card_state_by_id <full-id> -> state
  local wanted=$1 state file
  for state in "${STATES[@]}"; do
    for file in "$KB/$state"/*.md; do
      [[ -e $file ]] || continue
      if [[ $(fm_get "$file" id "") == "$wanted" ]]; then
        echo "$state"
        return 0
      fi
    done
  done
  return 1
}

dependency_state() { # dependency_state <card> -> ready|missing|<card-state>
  local dep state
  dep=$(fm_get "$1" depends_on "")
  if [[ -z $dep ]]; then echo ready; return; fi
  if state=$(card_state_by_id "$dep"); then echo "$state"; else echo missing; fi
}

refresh_dependency_cards() {
  local file dep state old title
  for file in "$KB/blocked"/*.md; do
    [[ -e $file ]] || continue
    [[ $(fm_get "$file" blocked_kind "") == dependency ]] || continue
    dep=$(fm_get "$file" depends_on "")
    state=$(dependency_state "$file")
    old=$(fm_get "$file" dependency_state "")
    if [[ $state == done ]]; then
      fm_set "$file" blocked_kind ""
      fm_set "$file" dependency_state ""
      printf 'dependency %s reached done; card returned to todo without consuming an attempt.\n' "$dep" |
        append_history "$file" "dependency ready"
      title=$(fm_get "$file" title "?")
      move_card "$file" todo >/dev/null
      echo "[$title] dependency $dep done -> todo"
    elif [[ $state != "$old" ]]; then
      fm_set "$file" dependency_state "$state"
      printf 'dependency %s changed state: %s -> %s; card remains blocked.\n' "$dep" "${old:-unknown}" "$state" |
        append_history "$file" "dependency wait"
    fi
  done
  for file in "$KB/todo"/*.md; do
    [[ -e $file ]] || continue
    dep=$(fm_get "$file" depends_on "")
    [[ -n $dep ]] || continue
    state=$(dependency_state "$file")
    [[ $state == done ]] && continue
    fm_set "$file" blocked_kind dependency
    fm_set "$file" dependency_state "$state"
    printf 'dependency %s is %s; card blocked before worker/reviewer start, so no attempt is consumed.\n' "$dep" "$state" |
      append_history "$file" "dependency wait"
    title=$(fm_get "$file" title "?")
    move_card "$file" blocked >/dev/null
    echo "[$title] dependency $dep is $state -> blocked"
  done
}

fail_card() { # fail_card <card> <infrastructure|worker|review|resolve|merge|dispatcher>
  fm_set "$1" failure_kind "$2"
  move_card "$1" failed
}

cmd_init() {
  local target=${1:-$PWD} base
  ROOT=$(kanban_project_root "$target") || die "Git repository required; initialize Git yourself before kanban init"
  base=$(kanban_board_dir "$target") || die "could not resolve the Git common directory"
  for s in "${STATES[@]}"; do mkdir -p "$base/$s"; done
  for s in "${STATES[@]}"; do touch "$base/$s/.gitkeep"; done
  mkdir -p "$base/briefs" "$base/reports" "$base/reviews"
  touch "$base/.gitignore"
  local ignored
  for ignored in wt/ briefs/ reports/ reviews/ .lock .dispatcher.lock .dispatcher.owner.* .merge.lock activity.jsonl activity.jsonl.lock .secretary-guard/; do
    grep -qxF "$ignored" "$base/.gitignore" || printf '%s\n' "$ignored" >>"$base/.gitignore"
  done
  if [[ ! -f $base/KANBAN.md ]]; then
    cat >"$base/KANBAN.md" <<'EOF'
---
backend_order: claude codex
default_backend: auto
default_model:
reviewer: auto
review_model: haiku
resolver: auto
resolve_model:
threshold: 80
max_attempts: 3
resolve_max_attempts: 2
review_infra_max_retries: 2
review_infra_backoff_seconds: 2
review_enabled: true
jobs: 4
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

## 外部操作カード

- ユーザーが明示したpush/deploy/publish等は `kanban add --operate` で起票する
- operatorはworktreeではなく本体checkoutで1回だけ動き、mergeと直列化され、reviewer審査を行わない
- operatorはカードに明記された外部操作だけを行う。実装変更や無関係なcommitへ広げない
- operatorは実行後のremote/deploy状態を確認できた時だけ、回答の先頭行を `OPERATION_OK: <確認内容>` にする。失敗・未確認・判断待ちは `BLOCKED: <理由>` にする

## 秘書契約 (最重要)

- **秘書はファイル重複・依存順序・実行中カードとの競合を理由に起票を保留しない。**
  明白な自己完結情報が揃い次第、即座に `todo` へ追加して dispatcher へ渡す。
- 秘書は競合調査・rebase/merge・修正・検証を一切行わない。それらは実行側
  (dispatcher/worker/reviewer/resolver) の責務であり、正式な状態遷移で処理する。
- 順序依存やファイル競合の解決は実行時に判明してから実行側が処理する。秘書へ
  戻さない。

## エージェント・モデル構成

- **既定方針: 上位モデル (fable / opus 等) は秘書・設計役だけ。手を動かすワーカーとレビュワー・resolver は下位モデルで十分**
- 既定: 通常実装は claude / sonnet。軽量カードは `-e low` または `--no-review`、軽微な修正は `-b codex -m gpt-5.3-codex-spark` (codex カードは -m 必須。model 名はバックエンド固有)、レビューは `review_model: haiku` 既定
- effort はカード単位で `-e low|medium|high|xhigh|max`。gpt-5.6-solは通常medium、難所highとし、共通設定のxhighを無条件に継承させない
- 設計・難所のカードだけ例外的に -m opus 等へ上げる (理由をカードに書く)
- resolver も既定では worker と同じ下位モデル (`resolver` / `resolve_model`)

## 依存関係・リリースゲート・失敗の意味

- ユーザーまたはこのポリシーが明示していない先行カードを、秘書がリリースゲートにしてはならない
- 明示された依存だけ `kanban add --depends-on <card-id>` で構造化する。依存先がdoneになるまで後続はworker/reviewerを起動せず、attemptを消費しない
- 実行中に真の依存が判明したworker/resolverは、失敗させず最初の行を `BLOCKED: <理由>` として終了する
- worker/reviewer/resolverはAskUserQuestion等の対話式選択肢を表示しない。worktree境界・policy・ユーザー判断と衝突したworkerは勝手に選ばず `BLOCKED: <必要な判断と理由>` を返す
- 質問UIを検出したdispatcherはattempt/reviewを消費せず `blocked_kind: user_input` に駐車し、秘書が判断を確認する。解決後に `kanban resume <id>` でtodoへ戻し、visible dispatchを再開する。resume自体はagentを起動しない
- `failed` は作業プロセスの失敗であり、製品の検証不合格を意味しない。`failure_kind` とHistoryから製品不具合・インフラ障害・未検証を区別する
- 依存で止めるのはpush/deploy等の不可逆な外部変更だけ。独立したtest/build/状態確認は止めず、別カードとして投入する
- インフラ障害で検証未実施なら、デプロイ不可と推測せず「未検証・ユーザー判断が必要」と報告する

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

- 通常カードは `--type` / `--size` / `--goal` / 1個以上の `--ac` / `--scope` で構造化し、
  `kanban ready --check <id>` 後に `kanban ready <id>` でbacklogからReadyへ移す。
- `--context` / `--out-of-scope` / 複数の `--verify` も必要に応じて明記する。会話文脈だけに完了条件を残さない
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
- worker並列数は既定4。`jobs:` / `KANBAN_JOBS` / `-j` は正の整数ならMornKanban側の上限なし（実機・API・Herdrの容量だけが制約）
- 秘書はユーザー指示による運用変更を `kanban config set jobs|default_backend|default_model|reviewer|review_model|resolver|resolve_model <value>` で行ってよい。project/boardファイルを直接編集しない
- 秘書は `kanban` のboard管理コマンドを実行してよい。`kanban run` だけは使わずvisible dispatchを使う。push/deploy等の直接実行はせず、`--operate` カードへ渡す
- 秘書自身が誤作成したカード、またはユーザーが破棄を指示した未着手カードは `kanban remove <id>` で即座に回収する。このコマンドはbacklog/Ready以外を拒否する
- Herdr は必須。実行モードを質問せず、利用不能なら停止・報告する。headless へフォールバックしない
- `dispatcher pane started` はペインへの起動要求が通っただけ。`dispatcher_failed` 通知時は `.git/kanban/wt/dispatcher.log` を読み、実際の終了理由を報告する。復旧目的でも `git init` / `commit` 等を勝手に行わない
- failed は秘書が即報告する。`blocked_kind: dependency` は自動再開、`user_input` / `scope_timebox` /
  `review_infra` は判断・復旧後に `kanban resume <id>` してdispatchする。`operation_unknown` は外部状態を確認し、
  成功済みなら `kanban operation <id> done`、安全に再試行できる時だけ `kanban operation <id> retry` を使う

## 秘書ペインの許可/禁止 (技術的ガード付き、詳細は MornKanban README の Secretary Guard)

**実装しない。検証しない。commit/push/tag しない。in-process agent を起動しない。カードを起票して visible Herdr へ dispatch する。**

- 許可: KANBAN.md/README/board の読み取り、`kanban inspect status|log|diff|diff-cached|show|branch`、
  `kanban` のboard管理コマンド、`kanban-secretary.sh bootstrap/dispatch/end`、ユーザーへの報告
- 禁止: project/boardファイルの直接編集・作成・削除（raw rmを含む）、build/test/lint/formatter/server 起動、
  headless agent CLI (`claude -p`/`codex exec`)、Claude/Codex の in-process Agent/Task/subagent、
  直接のgit（読み取りも設定済みhelperを起動し得るため禁止）、gitの変更系全般
  (add/commit/push/merge/rebase/reset/checkout/branch作成削除/tag/worktree等)、
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

- 許可: `.git/kanban/KANBAN.md` とボードの確認、全ての `kanban` CLI操作、
  `kanban-secretary.sh dispatch` / `dispatch --once`、ユーザーへの報告
- 禁止: `Agent`/`Task` (Claude Code)、collaboration/subagent 起動 (Codex)、
  `herdr-agent-worker.sh` 経由の visible pane を開かないその他の in-process delegation
- 違反に気づいたら即停止し、その成果は採用・merge せず、同じ依頼をカード化して
  dispatch へ回す
EOF
  fi
  echo "initialized $base"
}

cmd_migrate() {
  local target=${1:-$PWD} root legacy base path
  root=$(kanban_project_root "$target") || die "Git repository required"
  base=$(kanban_board_dir "$target") || die "could not resolve the Git common directory"
  legacy=$root/.kanban
  [[ -d $legacy ]] || die "no legacy board at $legacy"
  [[ ! -e $base ]] || die "destination already exists: $base"
  if [[ -f $legacy/.lock ]] && kill -0 "$(cat "$legacy/.lock")" 2>/dev/null; then
    die "dispatcher is running; stop it before migration"
  fi
  while IFS= read -r path; do
    case $path in
      "$legacy"/wt/*) die "registered card worktree still exists: $path" ;;
    esac
  done < <(git -C "$root" worktree list --porcelain | awk '/^worktree /{sub(/^worktree /, ""); print}')
  mv "$legacy" "$base"
  echo "migrated $legacy -> $base"
}

add_usage() {
  echo 'usage: kanban add "title" [-b claude|codex|auto] [-m model] [-e effort] [--depends-on card-id] [-t threshold] [--review|--no-review] [--diagnose|--operate] [--type TYPE --size SIZE --goal TEXT --ac TEXT --scope TEXT [--priority P] [--out-of-scope TEXT] [--context TEXT] [--verify CMD] [--ready]] < description'
}

cmd_add() {
  local arg
  for arg in "$@"; do
    [[ $arg == -- ]] && break
    if [[ $arg == -h || $arg == --help ]]; then add_usage; return 0; fi
  done
  require_root
  local title="" backend=$DEFAULT_BACKEND model=$DEFAULT_MODEL effort="" depends_on="" threshold=$DEFAULT_THRESHOLD
  local review_enabled=auto review_source=auto task_kind=implementation
  local card_type="" priority="" size="" goal="" scope="" out_of_scope="" context=""
  local structured=false ready_now=false
  # bash 3.2 + nounset treats an empty array as unset; index 0 is a sentinel.
  local -a acceptance=("") verification=("")
  while [[ $# -gt 0 ]]; do
    case $1 in
      -b|--backend) [[ $# -ge 2 ]] || die "$1 requires a value"; backend=$2; shift 2 ;;
      -m|--model) [[ $# -ge 2 ]] || die "$1 requires a value"; model=$2; shift 2 ;;
      -e|--effort) [[ $# -ge 2 ]] || die "$1 requires a value"; effort=$2; shift 2 ;;
      --depends-on) [[ $# -ge 2 ]] || die "$1 requires a value"; depends_on=$2; shift 2 ;;
      -t|--threshold) [[ $# -ge 2 ]] || die "$1 requires a value"; threshold=$2; shift 2 ;;
      --review) review_enabled=true; review_source=card; shift ;;
      --no-review) review_enabled=false; review_source=card; shift ;;
      --diagnose) [[ $task_kind == implementation ]] || die "only one task kind may be selected"; task_kind=diagnose; shift ;;
      --operate) [[ $task_kind == implementation ]] || die "only one task kind may be selected"; task_kind=operation; shift ;;
      --type) [[ $# -ge 2 ]] || die "$1 requires a value"; card_type=$2; structured=true; shift 2 ;;
      --priority) [[ $# -ge 2 ]] || die "$1 requires a value"; priority=$2; structured=true; shift 2 ;;
      --size) [[ $# -ge 2 ]] || die "$1 requires a value"; size=$2; structured=true; shift 2 ;;
      --goal) [[ $# -ge 2 ]] || die "$1 requires a value"; goal=$2; structured=true; shift 2 ;;
      --ac) [[ $# -ge 2 ]] || die "$1 requires a value"; acceptance[${#acceptance[@]}]=$2; structured=true; shift 2 ;;
      --scope) [[ $# -ge 2 ]] || die "$1 requires a value"; scope=$2; structured=true; shift 2 ;;
      --out-of-scope) [[ $# -ge 2 ]] || die "$1 requires a value"; out_of_scope=$2; structured=true; shift 2 ;;
      --context) [[ $# -ge 2 ]] || die "$1 requires a value"; context=$2; structured=true; shift 2 ;;
      --verify) [[ $# -ge 2 ]] || die "$1 requires a value"; verification[${#verification[@]}]=$2; structured=true; shift 2 ;;
      --ready) ready_now=true; structured=true; shift ;;
      --) shift; [[ $# -eq 1 && -z $title ]] || die "expected exactly one title after --"; title=$1; shift ;;
      -*) die "unknown option for kanban add: $1" ;;
      *) [[ -z $title ]] || die "unexpected argument: $1 (title is already set)"; title=$1; shift ;;
    esac
  done
  [[ -n $title ]] || { add_usage >&2; return 1; }
  case $backend in
    auto|claude|codex) ;;
    *) die "unknown backend: $backend (auto|claude|codex)" ;;
  esac
  validate_effort "$effort"
  [[ $threshold =~ ^[0-9]+$ && $threshold -le 100 ]] || die "threshold must be an integer from 0 to 100 (got: $threshold)"
  if [[ -n $depends_on ]] && ! card_state_by_id "$depends_on" >/dev/null; then
    die "dependency card not found: $depends_on"
  fi
  [[ $DEFAULT_DIAGNOSIS_TARGET_MINUTES =~ ^[1-9][0-9]*$ ]] || die "diagnosis_target_minutes must be a positive integer"
  [[ $DEFAULT_DIAGNOSIS_MAX_MINUTES =~ ^[1-9][0-9]*$ ]] || die "diagnosis_max_minutes must be a positive integer"
  [[ $DEFAULT_DIAGNOSIS_TARGET_MINUTES -le $DEFAULT_DIAGNOSIS_MAX_MINUTES ]] || die "diagnosis_target_minutes must not exceed diagnosis_max_minutes"
  if [[ $task_kind == diagnose && $review_enabled == auto ]]; then
    review_enabled=false
    review_source=diagnose
  fi
  if [[ $task_kind == operation ]]; then
    review_enabled=false
    review_source=operation
  fi
  local desc
  if [[ ! -t 0 ]]; then desc=$(cat); [[ -n $desc ]] || desc=$title; else desc=$title; fi
  local id slug file tmp state=todo
  $structured && state=backlog
  id=$(date +%Y%m%d-%H%M%S)-$RANDOM
  slug=$(echo "$title" | tr -cs '[:alnum:]' '-' | tr '[:upper:]' '[:lower:]' | cut -c1-40 | sed 's/-$//')
  file=$KB/$state/$id-${slug:-task}.md
  tmp=$(mktemp "$KB/$state/.card.XXXXXX")
  chmod 644 "$tmp"
  {
  cat <<EOF
---
id: $id
title: $title
card_schema: $($structured && echo structured || echo legacy)
type: $card_type
priority: $priority
size: $size
backend: $backend
model: $model
effort: $effort
depends_on: $depends_on
dependency_state:
blocked_kind:
failure_kind:
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

EOF
  if $structured; then
    cat <<EOF
## Goal

$goal

## Acceptance Criteria

EOF
    local item
    for item in "${acceptance[@]}"; do [[ -n $item ]] && printf -- '- %s\n' "$item"; done
    cat <<EOF

## Scope

$scope

## Out of Scope

$out_of_scope

## Context

$context

## Verification

EOF
    for item in "${verification[@]}"; do [[ -n $item ]] && printf -- '- `%s`\n' "$item"; done
    cat <<EOF

## Task

$desc

## History
EOF
  else
    cat <<EOF
## Task

$desc

## History
EOF
  fi
  } >"$tmp"
  if ! ln "$tmp" "$file" 2>/dev/null; then
    rm -f "$tmp"
    die "card id collision; retry add"
  fi
  rm -f "$tmp"
  if $ready_now; then
    if card_ready_errors "$file" >&2; then
      file=$(move_card "$file" todo)
    else
      echo "$file"
      return 1
    fi
  fi
  echo "$file"
}

ready_usage() { echo 'usage: kanban ready [--check] <backlog-card-id>'; }

cmd_ready() {
  if [[ ${1:-} == -h || ${1:-} == --help ]]; then ready_usage; return 0; fi
  require_root
  local check=false
  if [[ ${1:-} == --check ]]; then check=true; shift; fi
  [[ $# -eq 1 ]] || { ready_usage >&2; return 1; }
  local wanted=$1 file state id
  local -a hits=()
  [[ $wanted =~ ^[A-Za-z0-9-]+$ ]] || die "invalid card id: $wanted"
  for state in backlog todo; do
    for file in "$KB/$state"/*.md; do
      [[ -e $file ]] || continue
      id=$(fm_get "$file" id "")
      [[ $id == *"$wanted"* ]] && hits+=("$file")
    done
  done
  [[ ${#hits[@]} -gt 0 ]] || die "no backlog/ready card matching '$wanted'"
  [[ ${#hits[@]} -eq 1 ]] || die "'$wanted' matches multiple cards; use the full id"
  file=${hits[0]}
  if ! card_ready_errors "$file"; then return 1; fi
  if $check; then echo "ready check passed: $(fm_get "$file" id "?")"; return; fi
  [[ $file == "$KB/backlog/"* ]] || die "card is already ready"
  file=$(move_card "$file" todo)
  printf 'Definition of Ready passed.\n' | append_history "$file" "ready"
  echo "$file"
}

remove_usage() { echo 'usage: kanban remove <backlog-or-todo-card-id>'; }

cmd_remove() {
  if [[ ${1:-} == -h || ${1:-} == --help ]]; then remove_usage; return 0; fi
  require_root
  [[ $# -eq 1 ]] || { remove_usage >&2; return 1; }
  local wanted=$1 state file id title
  local -a hits=()
  [[ $wanted =~ ^[A-Za-z0-9-]+$ ]] || die "invalid card id: $wanted"
  for state in "${STATES[@]}"; do
    for file in "$KB/$state"/*.md; do
      [[ -e $file ]] || continue
      id=$(fm_get "$file" id "")
      [[ $id == *"$wanted"* ]] && hits+=("$file")
    done
  done
  [[ ${#hits[@]} -gt 0 ]] || die "no card matching '$wanted'"
  [[ ${#hits[@]} -eq 1 ]] || die "'$wanted' matches multiple cards; use the full id"
  file=${hits[0]}
  state=$(basename "$(dirname "$file")")
  [[ $state == backlog || $state == todo ]] || die "only todo cards or backlog cards can be removed (card is $state)"
  id=$(fm_get "$file" id "?")
  title=$(fm_get "$file" title "?")
  rm "$file"
  echo "removed $state card: $id  $title"
}

config_usage() {
  echo 'usage: kanban config set <jobs|default_backend|default_model|reviewer|review_model|resolver|resolve_model> <value>'
}

cmd_config() {
  if [[ ${1:-} == -h || ${1:-} == --help ]]; then config_usage; return 0; fi
  require_root
  [[ $# -eq 3 && $1 == set ]] || { config_usage >&2; return 1; }
  local key=$2 value=$3 shown
  case $key in
    jobs)
      [[ $value =~ ^[1-9][0-9]*$ ]] || die "jobs must be a positive integer (got: $value)"
      ;;
    default_backend|reviewer|resolver)
      case $value in auto|claude|codex) ;; *) die "$key must be auto, claude, or codex" ;; esac
      ;;
    default_model|review_model|resolve_model)
      if [[ $value == default ]]; then
        value=""
      else
        [[ $value =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]*$ ]] || die "invalid model: $value"
      fi
      ;;
    *) die "config key is not secretary-editable: $key" ;;
  esac
  fm_set "$KB/KANBAN.md" "$key" "$value"
  shown=${value:-default}
  echo "updated config: $key=$shown"
}

worker_prompt_for_card() { # worker_prompt_for_card <card>
  local file=$1 kind target maximum feedback decision
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
  elif [[ $kind == operation ]]; then
    cat <<'EOF'
OPERATOR CONTRACT
- You run in the project's main checkout, not a card worktree.
- Perform only the Git, publish, deploy, or other external mutation explicitly authorized by this card.
- Before pushing the parent repository, confirm every submodule's unpublished commits have been published to its remote.
- Do not change implementation or create unrelated commits. Verify and report the actual remote/deploy result.
- After verifying the real remote/deploy state, make the answer's first line: OPERATION_OK: <verified result>
- Never ask an interactive question. On failure, uncertainty, or missing authority, make the answer's first line: BLOCKED: <reason>

EOF
  fi
  card_task "$file"
  feedback=$(latest_rework_feedback "$file")
  if [[ -n $feedback ]]; then
    printf '\n## Latest reviewer feedback\n\n%s' "$feedback"
  fi
  decision=$(latest_user_decision "$file")
  if [[ -n $decision ]]; then
    printf '\n## User decision\n\n%s' "$decision"
  fi
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
  local state file
  local -a hits=()
  for state in "${STATES[@]}"; do
    for file in "$KB/$state"/*"$1"*.md; do [[ -e $file ]] && hits+=("$file"); done
  done
  [[ ${#hits[@]} -gt 0 ]] || die "no card matching '$1'"
  [[ ${#hits[@]} -eq 1 ]] || die "'$1' matches multiple cards; use the full id"
  local review_value
  review_value=$(effective_review_enabled "${hits[0]}")
  if [[ $review_value == false ]]; then echo "Review: OFF (fast iteration)"; else echo "Review: ON"; fi
  cat "${hits[0]}"
}

safe_git_inspect() { # isolated read-only Git metadata; repository config is never loaded
  local gitdir common temp format filemode status=0 item
  gitdir=$(command git -C "$ROOT" rev-parse --absolute-git-dir) || return
  common=$(command git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir) || return
  format=$(command git -C "$ROOT" rev-parse --show-object-format 2>/dev/null || echo sha1)
  filemode=$(command git -C "$ROOT" config --bool core.filemode 2>/dev/null || echo false)
  [[ $filemode == true || $filemode == false ]] || filemode=false
  temp=$(mktemp -d "${TMPDIR:-/tmp}/mornkanban-inspect.XXXXXX") || return
  mkdir -p "$temp/objects/info" "$temp/refs" "$temp/info"
  cp "$gitdir/HEAD" "$temp/HEAD"
  [[ -f $gitdir/index ]] && cp "$gitdir/index" "$temp/index"
  [[ -d $common/refs ]] && cp -R "$common/refs/." "$temp/refs/"
  for item in packed-refs shallow; do [[ -f $common/$item ]] && cp "$common/$item" "$temp/$item"; done
  [[ -f $common/info/exclude ]] && cp "$common/info/exclude" "$temp/info/exclude"
  printf '%s\n' "$common/objects" >"$temp/objects/info/alternates"
  if [[ $format == sha256 ]]; then
    printf '[core]\n\trepositoryformatversion = 1\n\tbare = false\n\tfilemode = %s\n[extensions]\n\tobjectFormat = sha256\n' "$filemode" >"$temp/config"
  else
    printf '[core]\n\trepositoryformatversion = 0\n\tbare = false\n\tfilemode = %s\n' "$filemode" >"$temp/config"
  fi
  GIT_DIR=$temp GIT_WORK_TREE=$ROOT GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    GIT_CONFIG_COUNT=0 GIT_OPTIONAL_LOCKS=0 GIT_PAGER=cat GIT_EXTERNAL_DIFF= \
    command git -c core.fsmonitor=false -c core.pager=cat "$@" || status=$?
  rm -rf "$temp"
  return "$status"
}

cmd_inspect() { # cmd_inspect <status|log|diff|diff-cached|show|branch>
  require_root
  local kind=${1:-} count ref
  shift || true
  case $kind in
    status)
      [[ $# -eq 0 ]] || die "usage: kanban inspect status"
      echo "note: filter/LFS-managed paths are compared as raw files and may be conservatively reported modified" >&2
      safe_git_inspect status --short --branch --untracked-files=all --ignore-submodules=all
      ;;
    log)
      count=${1:-20}
      [[ $# -le 1 && $count =~ ^[1-9][0-9]*$ && $count -le 200 ]] || die "usage: kanban inspect log [1-200]"
      safe_git_inspect log --no-ext-diff --no-textconv --no-color --oneline -n "$count"
      ;;
    diff|diff-cached)
      [[ $# -eq 0 ]] || die "usage: kanban inspect $kind"
      if [[ $kind == diff-cached ]]; then
        safe_git_inspect diff --cached --no-ext-diff --no-textconv --no-color --ignore-submodules=all --
      else
        echo "note: filter/LFS-managed paths are shown as raw files; configured filters are never executed" >&2
        safe_git_inspect diff --no-ext-diff --no-textconv --no-color --ignore-submodules=all --
      fi
      ;;
    show)
      ref=${1:-HEAD}
      [[ $# -le 1 && $ref != -* && $ref =~ ^[A-Za-z0-9._/~^{}-]+$ ]] || die "usage: kanban inspect show [ref]"
      safe_git_inspect show --no-ext-diff --no-textconv --no-color --end-of-options "$ref"
      ;;
    branch)
      [[ $# -eq 0 ]] || die "usage: kanban inspect branch"
      safe_git_inspect branch --no-color --no-column --list
      ;;
    *) die "usage: kanban inspect {status|log [1-200]|diff|diff-cached|show [ref]|branch}" ;;
  esac
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

worker_cmd() { # worker_cmd <backend> <model> <effort> (empty values inherit agent defaults)
  [[ -n ${KANBAN_WORKER_CMD:-} ]] || die "visible worker wrapper is required; run kanban-secretary.sh dispatch"
  echo "$KANBAN_WORKER_CMD"
}

review_cmd() { # review_cmd <card-effort>
  if [[ -n ${KANBAN_REVIEW_CMD:-} ]]; then echo "$KANBAN_REVIEW_CMD"; return; fi
  if [[ -n ${KANBAN_WORKER_CMD:-} ]]; then echo "env KANBAN_HERDR_ROLE=reviewer $KANBAN_WORKER_CMD"; return; fi
  die "visible reviewer wrapper is required; run kanban-secretary.sh dispatch"
}

resolve_cmd() { # resolve_cmd <card-backend> <card-model> <card-effort> -> resolver invocation
  if [[ -n ${KANBAN_RESOLVE_CMD:-} ]]; then echo "$KANBAN_RESOLVE_CMD"; return; fi
  if [[ -n ${KANBAN_WORKER_CMD:-} ]]; then echo "env KANBAN_HERDR_ROLE=resolver $KANBAN_WORKER_CMD"; return; fi
  die "visible resolver wrapper is required; run kanban-secretary.sh dispatch"
}

operation_cmd() { # operation_cmd <card-backend> <card-model> <card-effort>
  if [[ -n ${KANBAN_OPERATION_CMD:-} ]]; then echo "$KANBAN_OPERATION_CMD"; return; fi
  worker_cmd "$@"
}

detect_blocked() { # detect_blocked <worker-output> -> sets BLOCKED_REASON (empty = not blocked)
  BLOCKED_REASON=""
  local first_line
  first_line=$(printf '%s\n' "$1" | awk 'NF && $0 !~ /^herdr-agent-worker:/ {print; exit}')
  case $first_line in
    BLOCKED:*) BLOCKED_REASON=${first_line#BLOCKED:} ;;
  esac
}

operation_confirmed() { # operation_confirmed <worker-output>
  local first_line
  first_line=$(printf '%s\n' "$1" | awk 'NF && $0 !~ /^herdr-agent-worker:/ {print; exit}')
  [[ $first_line == OPERATION_OK:* ]]
}

parse_score() { # stdin: reviewer output -> "outcome<TAB>score<TAB>feedback" (empty on failure)
  python3 -c '
import json, sys
text = sys.stdin.read()
decoder = json.JSONDecoder()
objects = []
for i, char in enumerate(text):
    if char != "{":
        continue
    try:
        value, _ = decoder.raw_decode(text[i:])
        if isinstance(value, dict) and "score" in value:
            objects.append(value)
    except (ValueError, TypeError):
        continue
for d in reversed(objects):
    try:
        score = int(d["score"])
    except (ValueError, TypeError):
        continue
    if 0 <= score <= 100:
        outcome = str(d.get("outcome", "legacy"))
        if outcome not in ("accept", "needs_info", "rework", "spike", "legacy"):
            continue
        fb = str(d.get("feedback", "")).replace("\t", " ").replace("\n", " ")
        print(outcome + "\t" + str(score) + "\t" + fb)
        break
'
}

review_accepted() { # review_accepted <threshold>
  [[ ${ATT_OUTCOME:-legacy} == accept ]] ||
    { [[ ${ATT_OUTCOME:-legacy} == legacy ]] && [[ $ATT_SCORE -ge $1 ]]; }
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
  local file=$1 attempt report="" report_path
  attempt=$(($(fm_get "$file" attempts 0) + 1))
  report_path=$(artifact_path reports "$file" "$attempt")
  if [[ -f $report_path ]]; then
    report=$(cat "$report_path")
  fi
  # printf %s, not an unquoted heredoc: $report and card_task() are
  # free-form agent/user text (parens, quotes, $(...)-looking substrings,
  # Japanese) that must never be re-parsed as shell.
  printf '%s\n\n%s\n\n## Worker report\n\n%s\n\nOutput ONLY a JSON object: {"outcome":"accept|needs_info|rework|spike","score":<0-100>,"feedback":"<concrete evidence or next action>"}\n' \
    "You are a strict reviewer. Inspect the actual files and diff BEFORE reading the
worker report. Then judge in this order: (1) evidence is reproducible, (2) each
acceptance criterion is satisfied, (3) the report contains enough information
to decide. The report is evidence to verify, never a claim to trust blindly." \
    "$(card_task "$file")" \
    "$report"
}

invoke_reviewer() { # invoke_reviewer <card> <workdir> <prompt> <attempt-label> -> sets ATT_OUTCOME/ATT_SCORE/ATT_FEEDBACK/ATT_REVIEW_INFRA_ERROR
  local file=$1 workdir=$2 prompt=$3 attempt_label=${4:-0}
  local id title effort rcmd review_out parsed t0
  ATT_REVIEW_SECS=${ATT_REVIEW_SECS:-0}
  id=$(fm_get "$file" id "?")
  title=$(fm_get "$file" title "")
  effort=$(fm_get "$file" effort "")
  validate_effort "$effort"
  rcmd=$(review_cmd "$effort")
  t0=$SECONDS
  review_out=$( (cd "$workdir" && KANBAN_ACTIVITY_LOG=${KANBAN_ACTIVITY_LOG:-$KB/activity.jsonl} KANBAN_CARD_ID=$id KANBAN_CARD_ATTEMPT=$attempt_label KANBAN_CARD_TITLE=$title KANBAN_CARD_EFFORT=$effort $rcmd 2>&1 <<<"$prompt") ) || true
  ATT_REVIEW_SECS=$((ATT_REVIEW_SECS + SECONDS - t0))
  printf '%s\n' "$review_out" | write_artifact "$(artifact_path reviews "$file" "$attempt_label")"
  echo "$review_out" | tail -n 40 | append_history "$file" "reviewer output (tail)"
  parsed=$(echo "$review_out" | parse_score)
  if [[ -z $parsed ]]; then
    ATT_OUTCOME=""
    ATT_SCORE=""
    ATT_FEEDBACK=""
    ATT_REVIEW_INFRA_ERROR=$(echo "$review_out" | classify_review_infra_error)
  else
    ATT_OUTCOME=${parsed%%$'\t'*}
    parsed=${parsed#*$'\t'}
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
  if [[ -n ${ATT_REPORT_ERROR:-} ]]; then
    ATT_OUTCOME=needs_info
    ATT_SCORE=0
    ATT_FEEDBACK=$ATT_REPORT_ERROR
    ATT_REVIEW_INFRA_ERROR=""
    printf '{"outcome":"needs_info","score":0,"feedback":"%s"}\n' "$ATT_REPORT_ERROR" |
      write_artifact "$(artifact_path reviews "$file" "$attempt_label")"
    return
  fi
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
  local id backend model effort wcmd out title infra_cat retries t0 attempt_label task_kind timebox_secs prompt report_file missing
  ATT_BLOCKED_REASON=""
  ATT_BLOCKED_KIND=""
  ATT_WORKER_STATUS=0
  ATT_WORKER_SECS=0
  ATT_REVIEW_SECS=0
  ATT_REPORT_ERROR=""
  id=$(fm_get "$file" id "?")
  backend=$(fm_get "$file" backend "$DEFAULT_BACKEND")
  model=$(fm_get "$file" model "")
  effort=$(fm_get "$file" effort "")
  validate_effort "$effort"
  title=$(fm_get "$file" title "")
  task_kind=$(fm_get "$file" task_kind implementation)
  if [[ $task_kind == operation ]]; then
    wcmd=$(operation_cmd "$backend" "$model" "$effort")
  else
    wcmd=$(worker_cmd "$backend" "$model" "$effort")
  fi
  retries=$(fm_get "$file" worker_infra_retries 0)
  attempt_label=$(($(fm_get "$file" attempts 0) + 1))
  prompt=$(worker_prompt_for_card "$file")
  printf '%s\n' "$prompt" | write_artifact "$(artifact_path briefs "$file" "$attempt_label")"
  report_file=$(artifact_path reports "$file" "$attempt_label")
  timebox_secs=""
  if [[ $task_kind == diagnose ]]; then
    timebox_secs=$(($(fm_get "$file" diagnosis_max_minutes "$DEFAULT_DIAGNOSIS_MAX_MINUTES") * 60))
  fi
  while true; do
    # Custom worker commands (KANBAN_WORKER_CMD) receive the card's routing
    # via env, since the override bypasses worker_cmd's model handling.
    t0=$SECONDS
    ATT_WORKER_STATUS=0
    out=$( (cd "$workdir" && printf '%s\n' "$prompt" |
      KANBAN_CARD_ID=$id KANBAN_CARD_ATTEMPT=$attempt_label \
      KANBAN_ACTIVITY_LOG=${KANBAN_ACTIVITY_LOG:-$KB/activity.jsonl} \
      KANBAN_CARD_KIND=$task_kind KANBAN_CARD_TIMEBOX_SECS=$timebox_secs \
      KANBAN_CARD_MODEL=$model KANBAN_CARD_EFFORT=$effort KANBAN_CARD_BACKEND=$backend KANBAN_CARD_TITLE=$title $wcmd 2>&1) ) || ATT_WORKER_STATUS=$?
    ATT_WORKER_SECS=$((ATT_WORKER_SECS + SECONDS - t0))
    printf '%s\n' "$out" | write_artifact "$report_file"
    echo "$out" | tail -n 40 | append_history "$file" "worker output (tail)"
    if [[ $task_kind == operation ]] && { [[ $ATT_WORKER_STATUS -ne 0 ]] || ! operation_confirmed "$out"; }; then
      ATT_WORKER_INFRA_BLOCKED=false
      ATT_BLOCKED_KIND=operation_unknown
      ATT_BLOCKED_REASON=" external operation outcome is unknown; verify remote state, then use kanban operation <id> done|retry"
      ATT_SCORE=0
      ATT_FEEDBACK=""
      return
    fi
    infra_cat=$(echo "$out" | classify_worker_infra_error)
    if [[ $infra_cat == *scope_timebox* ]]; then
      ATT_WORKER_INFRA_BLOCKED=false
      ATT_BLOCKED_KIND=scope_timebox
      ATT_BLOCKED_REASON=" scope/timebox (hard maximum reached; partial evidence is in History if available)"
      ATT_SCORE=0
      ATT_FEEDBACK=""
      return
    fi
    if [[ $infra_cat == *agent_question* ]]; then
      ATT_WORKER_INFRA_BLOCKED=false
      ATT_BLOCKED_KIND=user_input
      ATT_BLOCKED_REASON=" interactive decision requested; clarify the card or project policy, then run kanban resume"
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
  if [[ $task_kind == operation ]] && ! operation_confirmed "$out"; then
    ATT_BLOCKED_KIND=operation_unknown
    ATT_BLOCKED_REASON=" external operation outcome is unknown; verify remote state, then use kanban operation <id> done|retry"
    ATT_SCORE=0
    ATT_FEEDBACK=""
  elif [[ -n $ATT_BLOCKED_REASON ]]; then
    ATT_BLOCKED_KIND=ordering
    ATT_SCORE=0
    ATT_FEEDBACK=""
  fi
  if [[ $(fm_get "$file" card_schema legacy) == structured && $task_kind == implementation &&
        $ATT_WORKER_STATUS -eq 0 && -z $ATT_BLOCKED_REASON ]]; then
    missing=$(report_missing_sections "$report_file")
    if [[ -n $missing ]]; then
      ATT_REPORT_ERROR="worker report is incomplete; missing sections: $missing"
    fi
  fi
}

record_attempt() { # record_attempt <card> <threshold> [checkpoint] -> increments attempts
  local file=$1 threshold=$2 checkpoint=${3:-} attempts timings outcome=${ATT_OUTCOME:-legacy} accepted=false
  attempts=$(($(fm_get "$file" attempts 0) + 1))
  timings="worker=${ATT_WORKER_SECS}s review=${ATT_REVIEW_SECS}s"
  if [[ $checkpoint == checkpoint ]]; then
    if review_accepted "$threshold"; then
      fm_update "$file" attempts "$attempts" last_timings "$timings" review_pending "" merge_pending 1 pass_result "$ATT_SCORE" review_outcome accept accepted_at "$(date '+%Y-%m-%dT%H:%M:%S')"
      accepted=true
    else
      fm_update "$file" attempts "$attempts" last_timings "$timings" review_pending "" review_outcome "$outcome"
    fi
  else
    if review_accepted "$threshold"; then
      fm_update "$file" attempts "$attempts" last_timings "$timings" review_outcome accept accepted_at "$(date '+%Y-%m-%dT%H:%M:%S')"
      accepted=true
    else
      fm_update "$file" attempts "$attempts" last_timings "$timings" review_outcome "$outcome"
    fi
  fi
  printf 'outcome: %s\nscore: %s / threshold: %s\nphase durations: %s\n\n%s\n' "$outcome" "$ATT_SCORE" "$threshold" "$timings" "$ATT_FEEDBACK" |
    append_history "$file" "review"
  if $accepted; then printf 'outcome: accept\nscore: %s\n' "$ATT_SCORE" | append_history "$file" "accepted"; fi
}

notify_result() { # notify_result <done|failed|blocked> <title> ; optional hook, never fatal
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

init_submodules() { # init_submodules <worktree-path> <log-file> -> git submodule update --init --recursive if the worktree has .gitmodules; no-op (and cheap) otherwise. On failure, repairs each worktree-local submodule gitdir from any refs/kanban-preserve/* preserve_submodule_objects left in the shared store and retries once -- a commit only reachable there (never pushed to origin) can't be cloned by the plain update, but is now checkoutable once fetched in.
  local wt=$1 log=$2
  [[ -f $wt/.gitmodules ]] || return 0
  git -C "$wt" submodule update --init --recursive >>"$log" 2>&1 && return 0
  restore_preserved_submodule_refs "$wt" "$log"
  git -C "$wt" submodule update --init --recursive >>"$log" 2>&1
}

restore_preserved_submodule_refs() { # restore_preserved_submodule_refs <worktree-path> <log-file> -> best-effort: for each submodule gitdir the worktree already has (partially initialized by the failed update above), fetch refs/kanban-preserve/* from the shared modules/ store into it. Never fails the caller -- a shared-store fetch miss just leaves the plain origin path as-is for the caller's retry.
  local wt=$1 log=$2 wt_git_dir common modules_root sub_gitdir rel target
  wt_git_dir=$(git -C "$wt" rev-parse --path-format=absolute --git-dir 2>/dev/null) || return 0
  modules_root=$wt_git_dir/modules
  [[ -d $modules_root ]] || return 0
  common=$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir) || return 0
  while IFS= read -r sub_gitdir; do
    rel=${sub_gitdir#"$modules_root"/}
    target=$common/modules/$rel
    [[ -d $target ]] || continue
    git -C "$target" for-each-ref --format='%(refname)' 'refs/kanban-preserve/' 2>/dev/null | grep -q . || continue
    git -C "$sub_gitdir" fetch -q "$target" '+refs/kanban-preserve/*:refs/kanban-preserve/*' >>"$log" 2>&1 ||
      echo "warning: failed to fetch preserved submodule refs for $rel from shared store $target" >>"$log"
  done < <(find "$modules_root" -type d -name objects 2>/dev/null | sed 's#/objects$##')
  return 0
}

preserve_submodule_objects() { # preserve_submodule_objects <worktree-path> -> fetch worktree-local submodule commits into the shared modules/ store before the worktree is destroyed; no-op without submodules
  local wt=$1 wt_git_dir modules_root common objdir sub_gitdir rel target head
  [[ -d $wt ]] || return 0
  wt_git_dir=$(git -C "$wt" rev-parse --path-format=absolute --git-dir 2>/dev/null) || return 0
  modules_root=$wt_git_dir/modules
  [[ -d $modules_root ]] || return 0
  common=$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir) || return 0
  while IFS= read -r objdir; do
    sub_gitdir=$(dirname "$objdir")
    rel=${sub_gitdir#"$modules_root"/}
    target=$common/modules/$rel
    head=$(git -C "$sub_gitdir" rev-parse HEAD 2>/dev/null) || continue
    mkdir -p "$target"
    git init -q "$target" >/dev/null 2>&1 || true
    # a dedicated namespaced ref keeps this fetch from touching whatever
    # branch is checked out in an already-initialized shared submodule store
    git -C "$sub_gitdir" update-ref "refs/kanban-preserve/$head" HEAD 2>/dev/null || continue
    git -C "$target" fetch -q "$sub_gitdir" "+refs/kanban-preserve/$head:refs/kanban-preserve/$head" 2>/dev/null || true
  done < <(find "$modules_root" -type d -name objects 2>/dev/null)
}

submodule_gitlink_diff() { # submodule_gitlink_diff <topic-branch> <base-branch> -> "sha\tpath" lines for every gitlink topic changed vs their merge-base with base; no output if there is no common ancestor
  local topic=$1 base=$2 mb
  mb=$(git -C "$ROOT" merge-base "$topic" "$base" 2>/dev/null) || return 0
  git -C "$ROOT" diff --raw --no-abbrev "$mb" "$topic" -- 2>/dev/null | awk '$2=="160000"{print $4"\t"$NF}'
}

verify_submodule_gitlinks() { # verify_submodule_gitlinks <topic-branch> <base-branch> -> 0 if every gitlink topic changed vs base is reachable in the shared modules/ store; else 1 with VERIFY_SUBMODULE_REASON set
  local topic=$1 base=$2 common sha path
  VERIFY_SUBMODULE_REASON=""
  common=$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir) || return 0
  while IFS=$'\t' read -r sha path; do
    [[ -n $sha && -n $path ]] || continue
    # module directory name mirrors the submodule path (git's default when
    # `submodule add` is called without --name, the only form this project uses)
    if ! git -C "$common/modules/$path" cat-file -e "${sha}^{commit}" 2>/dev/null; then
      VERIFY_SUBMODULE_REASON="submodule '$path' gitlink $sha is unreachable in the shared object store ($common/modules/$path)"
      return 1
    fi
  done < <(submodule_gitlink_diff "$topic" "$base")
  return 0
}

submodule_publish_card_pending() { # submodule_publish_card_pending <path> -> 0 if an undispatched (backlog/todo) publish card already exists for that submodule path
  local path=$1 f
  for f in "$KB"/backlog/*.md "$KB"/todo/*.md; do
    [[ -e $f ]] || continue
    [[ $(fm_get "$f" title "") == "Publish submodule: $path" ]] && return 0
  done
  return 1
}

enqueue_submodule_publish_cards() { # enqueue_submodule_publish_cards <topic-branch> <base-branch> -> files an --operate backlog card per gitlink topic changed vs base, unless an undispatched card for that path already exists; merge is never blocked by this (best-effort, failures are logged and swallowed)
  local topic=$1 base=$2 sha path
  while IFS=$'\t' read -r sha path; do
    [[ -n $sha && -n $path ]] || continue
    submodule_publish_card_pending "$path" && continue
    if ! printf 'packaged/non-packaged の判定と公開手順は ~/docs/morn/submodule-commit.md を参照する。\n' |
      ( cmd_add "Publish submodule: $path" --operate --type operation \
          --goal "submodule $path の commit を remote へ publish し、親リポの gitlink 参照切れを防ぐ" \
          --ac "submodule $path の commit $sha が remote へ push されている" \
          --scope "submodule $path の publish のみ。他のsubmoduleや親リポの実装変更は対象外" \
          --context "path: $path / new sha: $sha" >/dev/null ); then
      echo "warning: failed to enqueue submodule publish card for $path (sha $sha)" >&2
    fi
  done < <(submodule_gitlink_diff "$topic" "$base")
}

kanban_remove_worktree() { # kanban_remove_worktree <worktree-path> -> preserve submodule objects, then remove
  preserve_submodule_objects "$1"
  git -C "$ROOT" worktree remove --force "$1" 2>/dev/null || true
}

review_prompt_for_resolve() { # review_prompt_for_resolve <card> <card_branch> <base_branch>
  local file=$1 card_branch=$2 base_branch=$3
  # printf %s: card_branch/base_branch are refnames (safe), but card_task()
  # is free-form card text and must never be re-parsed as shell (see
  # review_prompt_for_card).
  printf '%s\n\n%s\n\nOutput ONLY a JSON object: {"outcome":"accept|needs_info|rework|spike","score":<0-100>,"feedback":"<concrete evidence or next action>"}\n' \
    "You are a strict reviewer. This worktree is the result of resolving a merge
conflict between card branch $card_branch and base branch $base_branch.
Inspect the actual files and diffs; do not trust the resolver's claims. Judge
whether BOTH sides' intent was preserved and the task below is genuinely
complete." \
    "$(card_task "$file")"
}

run_resolve_attempt() { # run_resolve_attempt <card> <resolve-workdir> <conflict-files> <base-branch> <card-branch> -> sets ATT_UNRESOLVED/ATT_RESOLVE_SECS
  # Runs ONLY the resolver step (agent + commit); sets ATT_UNRESOLVED=true
  # when conflict markers remain. Review is a separate step -- see
  # review_with_infra_retry -- so an infra failure there never re-runs the
  # resolver.
  local file=$1 workdir=$2 conflict_files=$3 base_branch=$4 card_branch=$5
  local id backend model effort wcmd out title prompt t0 attempt_label status=0 infra_cat decision
  ATT_RESOLVE_SECS=0
  ATT_REVIEW_SECS=0
  ATT_RESOLVE_BLOCKED_KIND=""
  ATT_RESOLVE_BLOCKED_REASON=""
  id=$(fm_get "$file" id "?")
  backend=$(fm_get "$file" backend "$DEFAULT_BACKEND")
  model=$(fm_get "$file" model "")
  effort=$(fm_get "$file" effort "")
  validate_effort "$effort"
  title=$(fm_get "$file" title "")
  wcmd=$(resolve_cmd "$backend" "$model" "$effort")
  attempt_label="resolve-$(($(fm_get "$file" resolve_attempts 0) + 1))"
  prompt=$(printf 'You are the conflict-resolution role for MornKanban. Card branch %s passed review but conflicts with the current base branch %s. Resolve the conflict in this worktree, preserving the intent of BOTH sides -- never simply discard one side. Run any tests the task requires, then stage every resolved, created, or deleted file with git add/rm and leave the tree conflict-free.\n\nConflicted files:\n%s\n\nOriginal task:\n%s\n' \
    "$card_branch" "$base_branch" "$conflict_files" "$(card_task "$file")")
  decision=$(latest_user_decision "$file")
  if [[ -n $decision ]]; then
    prompt=$(printf '%s\n## User decision\n\n%s' "$prompt" "$decision")
  fi
  t0=$SECONDS
  out=$( (cd "$workdir" && printf '%s' "$prompt" |
    KANBAN_CARD_ID=$id KANBAN_CARD_ATTEMPT=$attempt_label \
    KANBAN_ACTIVITY_LOG=${KANBAN_ACTIVITY_LOG:-$KB/activity.jsonl} \
    KANBAN_CARD_MODEL=$model KANBAN_CARD_EFFORT=$effort KANBAN_CARD_BACKEND=$backend KANBAN_CARD_TITLE=$title \
    KANBAN_CONFLICT_FILES=$conflict_files KANBAN_BASE_BRANCH=$base_branch KANBAN_CARD_BRANCH=$card_branch \
    $wcmd 2>&1) ) || status=$?
  ATT_RESOLVE_SECS=$((SECONDS - t0))
  echo "$out" | tail -n 40 | append_history "$file" "resolver output (tail)"
  ATT_UNRESOLVED=false
  infra_cat=$(echo "$out" | classify_worker_infra_error)
  detect_blocked "$out"
  if [[ -n $infra_cat || $status -ne 0 ]]; then
    ATT_RESOLVE_BLOCKED_KIND=review_infra
    ATT_RESOLVE_BLOCKED_REASON="resolver process failed before a safe commit (exit $status${infra_cat:+; $infra_cat})"
    return
  fi
  if [[ -n $BLOCKED_REASON ]]; then
    ATT_RESOLVE_BLOCKED_KIND=user_input
    ATT_RESOLVE_BLOCKED_REASON=$BLOCKED_REASON
    return
  fi
  if git -C "$workdir" ls-files -u | grep -q . || ! git -C "$workdir" diff --check >/dev/null; then
    ATT_UNRESOLVED=true
    return
  fi
  if ! git -C "$workdir" diff --quiet || [[ -n $(git -C "$workdir" ls-files --others --exclude-standard) ]]; then
    ATT_UNRESOLVED=true
    return
  fi
  if ! git -C "$workdir" diff --cached --check >/dev/null; then
    ATT_UNRESOLVED=true
    return
  fi
  git -C "$workdir" commit -q --allow-empty -m "kanban: resolve conflict for $title"
}

record_resolve_attempt() { # record_resolve_attempt <card> <threshold> -> increments resolve_attempts
  local file=$1 threshold=$2 attempts timings outcome=${ATT_OUTCOME:-legacy} accepted=false
  attempts=$(($(fm_get "$file" resolve_attempts 0) + 1))
  timings="resolver=${ATT_RESOLVE_SECS}s review=${ATT_REVIEW_SECS}s"
  if review_accepted "$threshold"; then
    fm_update "$file" resolve_attempts "$attempts" last_timings "$timings" resolve_review_pending "" resolve_merge_pending 1 pass_result "$ATT_SCORE" review_outcome accept accepted_at "$(date '+%Y-%m-%dT%H:%M:%S')"
    accepted=true
  else
    fm_update "$file" resolve_attempts "$attempts" last_timings "$timings" resolve_review_pending "" review_outcome "$outcome"
  fi
  printf 'outcome: %s\nscore: %s / threshold: %s\nphase durations: %s\n\n%s\n' "$outcome" "$ATT_SCORE" "$threshold" "$timings" "$ATT_FEEDBACK" |
    append_history "$file" "resolve review"
  if $accepted; then printf 'outcome: accept\nscore: %s\n' "$ATT_SCORE" | append_history "$file" "accepted (resolve)"; fi
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
  ATT_SCORE=0

  [[ $file == "$KB/resolving/"* ]] || move_card "$file" resolving >/dev/null
  file=$KB/resolving/$(basename "$file")
  fm_set "$file" resume_phase ""
  [[ -n $card_wt ]] && kanban_remove_worktree "$card_wt"

  # Resuming a review-infra-blocked resolve card (see cmd_resume): the
  # resolve worktree/branch already survived the block, reuse them instead
  # of failing on `worktree add`'s "branch already exists".
  local initial_merge_error=false
  if git -C "$ROOT" show-ref --verify --quiet "refs/heads/$resolve_branch" && [[ -d $resolve_wt ]]; then
    echo "$tag resuming existing resolve worktree/branch"
    if ! init_submodules "$resolve_wt" "$KB/wt/$id.log"; then
      echo "resolve submodule init failed; see .git/kanban/wt/$id.log" | append_history "$file" "error"
      git -C "$ROOT" branch -q -D "$card_branch" 2>/dev/null || true
      fail_card "$file" infrastructure >/dev/null
      echo "$tag FAIL resolve submodule init failed -> failed"
      notify_result failed "$title"
      return
    fi
  elif ! git -C "$ROOT" worktree add -q -b "$resolve_branch" "$resolve_wt" "$base_branch" 2>>"$KB/wt/$id.log"; then
    echo "resolve worktree add failed; see .git/kanban/wt/$id.log" | append_history "$file" "error"
    git -C "$ROOT" branch -q -D "$card_branch" 2>/dev/null || true
    fail_card "$file" infrastructure >/dev/null
    echo "$tag FAIL resolve worktree add failed -> failed"
    notify_result failed "$title"
    return
  elif ! init_submodules "$resolve_wt" "$KB/wt/$id.log"; then
    echo "resolve submodule init failed; see .git/kanban/wt/$id.log" | append_history "$file" "error"
    git -C "$ROOT" branch -q -D "$card_branch" 2>/dev/null || true
    fail_card "$file" infrastructure >/dev/null
    echo "$tag FAIL resolve submodule init failed -> failed"
    notify_result failed "$title"
    return
  else
    if ! git -C "$resolve_wt" merge --no-ff -q -m "kanban: merge $card_branch for conflict resolution" "$card_branch" \
      2>>"$KB/wt/$id.log"; then
      git -C "$resolve_wt" ls-files -u | grep -q . || initial_merge_error=true
    fi
  fi

  if $initial_merge_error; then
    echo "resolve merge failed without producing conflict entries; branches are kept" | append_history "$file" "error"
    kanban_remove_worktree "$resolve_wt"
    fail_card "$file" merge >/dev/null
    echo "$tag FAIL resolve merge setup failed -> failed (branches kept)"
    notify_result failed "$title"
    return
  fi
  if [[ -z $conflict_files ]]; then
    conflict_files=$(git -C "$resolve_wt" diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ' ')
  fi

  local resolved=false blocked_infra=false blocked_resolver=false resolver_block_kind="" resolver_block_reason=""
  if [[ $(fm_get "$file" resolve_merge_pending "") == 1 ]]; then
    resolved=true
    ATT_SCORE=$(fm_get "$file" pass_result 0)
    echo "$tag resuming pending resolve merge"
  fi
  while ! $resolved && [[ $resolve_attempts -lt $resolve_max_attempts ]]; do
    echo "$tag resolve attempt $((resolve_attempts + 1))/$resolve_max_attempts (branch: $resolve_branch)"
    if [[ $(fm_get "$file" resolve_review_pending "") == 1 ]]; then
      ATT_RESOLVE_SECS=0
      ATT_REVIEW_SECS=0
      ATT_UNRESOLVED=false
      echo "$tag resuming pending resolve review"
    else
      run_resolve_attempt "$file" "$resolve_wt" "$conflict_files" "$base_branch" "$card_branch"
      if [[ -n $ATT_RESOLVE_BLOCKED_KIND ]]; then
        blocked_resolver=true
        resolver_block_kind=$ATT_RESOLVE_BLOCKED_KIND
        resolver_block_reason=$ATT_RESOLVE_BLOCKED_REASON
        break
      fi
    fi
    if $ATT_UNRESOLVED; then
      resolve_attempts=$((resolve_attempts + 1))
      fm_set "$file" resolve_attempts "$resolve_attempts"
      printf 'conflict markers remain unresolved after the resolver attempt.\n' | append_history "$file" "resolve review"
      echo "$tag RESOLVE RETRY conflict markers remain"
      continue
    fi
    if [[ $review_enabled != true ]]; then
      resolve_attempts=$((resolve_attempts + 1))
      fm_update "$file" resolve_attempts "$resolve_attempts" resolve_merge_pending 1 pass_result 0
      echo "review skipped: review_enabled=false (source: $review_source)" | append_history "$file" "resolve review"
      resolved=true
      break
    fi
    fm_set "$file" resolve_review_pending 1
    review_with_infra_retry "$file" "$resolve_wt" "$review_infra_max" \
      "$(review_prompt_for_resolve "$file" "$card_branch" "$base_branch")" \
      "resolve-$((resolve_attempts + 1))"
    if $ATT_REVIEW_INFRA_BLOCKED; then
      blocked_infra=true
      break
    fi
    record_resolve_attempt "$file" "$threshold"
    resolve_attempts=$((resolve_attempts + 1))
    if review_accepted "$threshold"; then
      resolved=true
      break
    fi
    if [[ ${ATT_OUTCOME:-} == spike ]]; then
      fm_set "$file" blocked_kind review_decision
      printf '%s\n' "$ATT_FEEDBACK" | append_history "$file" "rework instruction (spike decision required)"
      move_card "$file" blocked >/dev/null
      echo "$tag BLOCKED reviewer requested spike -> blocked (branches kept)"
      notify_result blocked "$title"
      return
    fi
    if [[ ${ATT_OUTCOME:-legacy} == legacy ]]; then
      echo "$tag RESOLVE RETRY score=$ATT_SCORE"
    else
      echo "$tag RESOLVE RETRY outcome=$ATT_OUTCOME score=$ATT_SCORE"
    fi
    printf '%s\n' "$ATT_FEEDBACK" | append_history "$file" "rework instruction (fix these points)"
  done

  if $blocked_resolver; then
    fm_set "$file" blocked_kind "$resolver_block_kind"
    printf '%s\nbranches %s and %s, and the resolve worktree, are kept.\nrecovery: verify the cause, then kanban resume %s\n' \
      "$resolver_block_reason" "$resolve_branch" "$card_branch" "$id" |
      append_history "$file" "blocked (resolver)"
    move_card "$file" blocked >/dev/null
    echo "$tag BLOCKED resolver kind=$resolver_block_kind -> blocked"
    notify_result blocked "$title"
    return
  fi

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
    kanban_remove_worktree "$resolve_wt"
    fail_card "$file" resolve >/dev/null
    if [[ $review_enabled == true ]]; then
      echo "$tag FAIL resolve score=$ATT_SCORE attempts exhausted -> failed (branches $resolve_branch, $card_branch kept)"
    else
      echo "$tag FAIL unresolved conflict (review disabled) -> failed (branches $resolve_branch, $card_branch kept)"
    fi
    notify_result failed "$title"
    return
  fi

  local merge_t0=$SECONDS merge_secs
  local current_branch
  merge_lock acquire
  current_branch=$(git -C "$ROOT" symbolic-ref --short HEAD 2>/dev/null || true)
  if [[ $current_branch != "$base_branch" ]]; then
    merge_lock release
    fm_set "$file" blocked_kind main_branch_changed
    printf 'main checkout branch changed from %s to %s before resolve merge; no merge was attempted.\n' "$base_branch" "${current_branch:-detached}" |
      append_history "$file" "blocked (main branch changed)"
    move_card "$file" blocked >/dev/null
    echo "$tag BLOCKED main branch changed -> blocked (resolve branch kept)"
    notify_result blocked "$title"
    return
  fi
  preserve_submodule_objects "$resolve_wt"
  if ! verify_submodule_gitlinks "$resolve_branch" "$base_branch"; then
    merge_lock release
    printf '%s\nresolve branch %s and original card branch %s are kept for manual inspection.\n' \
      "$VERIFY_SUBMODULE_REASON" "$resolve_branch" "$card_branch" | append_history "$file" "submodule objects unreachable"
    fail_card "$file" merge >/dev/null
    echo "$tag FAIL submodule gitlink unreachable -> failed (branches $resolve_branch, $card_branch kept)"
    notify_result failed "$title"
    return
  fi
  if git -C "$ROOT" merge-base --is-ancestor "$resolve_branch" HEAD 2>/dev/null; then
    merge_lock release
    kanban_remove_worktree "$resolve_wt"
    git -C "$ROOT" branch -q -D "$resolve_branch" "$card_branch" 2>/dev/null || true
    fm_set "$file" merged_at "$(date '+%Y-%m-%dT%H:%M:%S')"
    echo "merge was already present on $base_branch" | append_history "$file" "merged"
    move_card "$file" done >/dev/null
    echo "$tag PASS resolve -> done (merge was already present on $base_branch)"
    notify_result done "$title"
    return
  fi
  if git -C "$ROOT" merge --no-ff -q -m "kanban: $title (conflict resolved)" "$resolve_branch" 2>>"$KB/wt/$id.log"; then
    merge_lock release
    merge_secs=$((SECONDS - merge_t0))
    fm_set "$file" merged_at "$(date '+%Y-%m-%dT%H:%M:%S')"
    echo "phase durations: merge=${merge_secs}s" | append_history "$file" "merged"
    enqueue_submodule_publish_cards "$resolve_branch" "$base_branch"
    kanban_remove_worktree "$resolve_wt"
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
    kanban_remove_worktree "$resolve_wt"
    local resolve_note
    if [[ $review_enabled == true ]]; then resolve_note="resolve passed review (score $ATT_SCORE)"; else resolve_note="resolve completed (review disabled)"; fi
    printf '%s but merging %s into %s failed; branches %s and %s kept for manual merge.\n' \
      "$resolve_note" "$resolve_branch" "$base_branch" "$resolve_branch" "$card_branch" |
      append_history "$file" "merge conflict (post-resolve)"
    fail_card "$file" merge >/dev/null
    echo "$tag CONFLICT (post-resolve) -> failed (branches kept; merge manually)"
    notify_result failed "$title"
  fi
}

process_card_seq() { # serialized operation cards run in the main checkout
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
    local blocked_kind=${ATT_BLOCKED_KIND:-ordering}
    fm_set "$file" blocked_kind "$blocked_kind"
    printf 'worker stopped without consuming an attempt (kind: %s):%s\n' "$blocked_kind" "$ATT_BLOCKED_REASON" |
      append_history "$file" "blocked"
    move_card "$file" blocked >/dev/null
    echo "    BLOCKED kind=$blocked_kind ->$ATT_BLOCKED_REASON"
    case $blocked_kind in
      dependency) : ;;
      *) notify_result blocked "$title" ;;
    esac
    return
  fi

  if [[ $ATT_WORKER_STATUS -ne 0 ]]; then
    attempts=$((attempts + 1))
    fm_set "$file" attempts "$attempts"
    echo "worker exited with status $ATT_WORKER_STATUS; reviewer was not run" | append_history "$file" "worker failure"
    fail_card "$file" worker >/dev/null
    echo "    FAIL worker exit=$ATT_WORKER_STATUS -> failed (review skipped)"
    notify_result failed "$title"
    return
  fi

  if [[ $review_enabled != true && -z $ATT_REPORT_ERROR ]]; then
    attempts=$((attempts + 1))
    fm_set "$file" attempts "$attempts"
    echo "review skipped: review_enabled=false (source: $review_source)" | append_history "$file" "review"
    move_card "$file" done >/dev/null
    echo "    PASS (review disabled) -> done"
    notify_result done "$title"
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

  if review_accepted "$threshold"; then
    move_card "$file" done >/dev/null
    echo "    PASS score=$ATT_SCORE -> done"
    notify_result done "$title"
  elif [[ ${ATT_OUTCOME:-} == spike ]]; then
    fm_set "$file" blocked_kind review_decision
    printf '%s\n' "$ATT_FEEDBACK" | append_history "$file" "rework instruction (spike decision required)"
    move_card "$file" blocked >/dev/null
    echo "    BLOCKED reviewer requested spike -> blocked"
    notify_result blocked "$title"
  elif [[ $attempts -ge $max_attempts ]]; then
    fail_card "$file" review >/dev/null
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
    echo "worktree add failed; see .git/kanban/wt/$id.log" | append_history "$file" "error"
    fail_card "$file" infrastructure >/dev/null
    echo "$tag FAIL worktree add failed -> failed"
    notify_result failed "$title"
    return
  fi
  if ! init_submodules "$wt" "$KB/wt/$id.log"; then
    echo "submodule init failed; see .git/kanban/wt/$id.log" | append_history "$file" "error"
    fail_card "$file" infrastructure >/dev/null
    echo "$tag FAIL submodule init failed -> failed"
    notify_result failed "$title"
    return
  fi

  local passed=false blocked_infra=false
  if [[ $(fm_get "$file" merge_pending "") == 1 ]]; then
    passed=true
    ATT_SCORE=$(fm_get "$file" pass_result 0)
    echo "$tag resuming pending merge (branch: $branch)"
  fi
  while ! $passed && [[ $attempts -lt $max_attempts ]]; do
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
        local blocked_kind=${ATT_BLOCKED_KIND:-ordering}
        fm_set "$file" blocked_kind "$blocked_kind"
        printf 'worker stopped without consuming an attempt (kind: %s):%s\nworktree is discarded.\n' \
          "$blocked_kind" "$ATT_BLOCKED_REASON" | append_history "$file" "blocked"
        kanban_remove_worktree "$wt"
        git -C "$ROOT" branch -q -D "$branch" 2>/dev/null || true
        move_card "$file" blocked >/dev/null
        echo "$tag BLOCKED kind=$blocked_kind ->$ATT_BLOCKED_REASON"
        case $blocked_kind in
          dependency) : ;;
          *) notify_result blocked "$title" ;;
        esac
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
      if [[ $ATT_WORKER_STATUS -ne 0 ]]; then
        attempts=$((attempts + 1))
        fm_set "$file" attempts "$attempts"
        git -C "$wt" add -A
        git -C "$wt" commit -q --allow-empty -m "kanban: $title (failed attempt $attempts)"
        echo "worker exited with status $ATT_WORKER_STATUS; reviewer was not run" | append_history "$file" "worker failure"
        kanban_remove_worktree "$wt"
        fail_card "$file" worker >/dev/null
        echo "$tag FAIL worker exit=$ATT_WORKER_STATUS -> failed (branch $branch kept; review skipped)"
        notify_result failed "$title"
        return
      fi
      git -C "$wt" add -A
      git -C "$wt" commit -q --allow-empty -m "kanban: $title (attempt $((attempts + 1)))"
      if [[ $review_enabled != true && -z $ATT_REPORT_ERROR ]]; then
        attempts=$((attempts + 1))
        fm_update "$file" attempts "$attempts" merge_pending 1 pass_result 0
        passed=true
        echo "review skipped: review_enabled=false (source: $review_source)" | append_history "$file" "review"
        break
      fi
      fm_set "$file" review_pending 1
    fi

    review_with_infra_retry "$file" "$wt" "$review_infra_max" "$(review_prompt_for_card "$file")" "$((attempts + 1))"
    if $ATT_REVIEW_INFRA_BLOCKED; then
      blocked_infra=true
      break
    fi
    record_attempt "$file" "$threshold" checkpoint
    attempts=$((attempts + 1))
    if review_accepted "$threshold"; then
      passed=true
      break
    fi
    if [[ ${ATT_OUTCOME:-} == spike ]]; then
      fm_set "$file" blocked_kind review_decision
      printf '%s\n' "$ATT_FEEDBACK" | append_history "$file" "rework instruction (spike decision required)"
      move_card "$file" blocked >/dev/null
      echo "$tag BLOCKED reviewer requested spike -> blocked (branch/worktree kept)"
      notify_result blocked "$title"
      return
    fi
    if [[ ${ATT_OUTCOME:-legacy} == legacy ]]; then
      echo "$tag RETRY score=$ATT_SCORE"
    else
      echo "$tag RETRY outcome=$ATT_OUTCOME score=$ATT_SCORE"
    fi
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
    kanban_remove_worktree "$wt"
    if [[ $review_enabled == true || -n ${ATT_REPORT_ERROR:-} ]]; then
      fail_card "$file" review >/dev/null
      echo "$tag FAIL outcome=${ATT_OUTCOME:-legacy} score=$ATT_SCORE attempts exhausted -> failed (branch $branch kept)"
    else
      fail_card "$file" worker >/dev/null
      echo "$tag FAIL worker exit=$ATT_WORKER_STATUS (review disabled) -> failed (branch $branch kept)"
    fi
    notify_result failed "$title"
    return
  fi

  local merge_t0=$SECONDS merge_secs
  local current_branch
  merge_lock acquire
  current_branch=$(git -C "$ROOT" symbolic-ref --short HEAD 2>/dev/null || true)
  if [[ $current_branch != "$base_branch" ]]; then
    merge_lock release
    fm_set "$file" blocked_kind main_branch_changed
    printf 'main checkout branch changed from %s to %s before merge; no merge was attempted.\n' "$base_branch" "${current_branch:-detached}" |
      append_history "$file" "blocked (main branch changed)"
    move_card "$file" blocked >/dev/null
    echo "$tag BLOCKED main branch changed -> blocked (branch $branch kept)"
    notify_result blocked "$title"
    return
  fi
  preserve_submodule_objects "$wt"
  if ! verify_submodule_gitlinks "$branch" "$base_branch"; then
    merge_lock release
    printf '%s\nbranch %s is kept for manual inspection.\n' "$VERIFY_SUBMODULE_REASON" "$branch" |
      append_history "$file" "submodule objects unreachable"
    fail_card "$file" merge >/dev/null
    echo "$tag FAIL submodule gitlink unreachable -> failed (branch $branch kept)"
    notify_result failed "$title"
    return
  fi
  if git -C "$ROOT" merge-base --is-ancestor "$branch" HEAD 2>/dev/null; then
    merge_lock release
    kanban_remove_worktree "$wt"
    git -C "$ROOT" branch -q -D "$branch" 2>/dev/null || true
    rm -f "$KB/wt/$id.log"
    fm_set "$file" merged_at "$(date '+%Y-%m-%dT%H:%M:%S')"
    echo "merge was already present on $base_branch" | append_history "$file" "merged"
    move_card "$file" done >/dev/null
    echo "$tag PASS -> done (merge was already present on $base_branch)"
    notify_result done "$title"
    return
  fi
  if git -C "$ROOT" merge --no-ff -q -m "kanban: $title" "$branch" 2>>"$KB/wt/$id.log"; then
    merge_lock release
    merge_secs=$((SECONDS - merge_t0))
    fm_set "$file" merged_at "$(date '+%Y-%m-%dT%H:%M:%S')"
    echo "phase durations: merge=${merge_secs}s" | append_history "$file" "merged"
    enqueue_submodule_publish_cards "$branch" "$base_branch"
    kanban_remove_worktree "$wt"
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
    fm_update "$file" merge_pending "" resume_phase resolve
    process_resolve_wt "$file" "$base_branch" "$branch" "$wt" "$conflict_files"
  fi
}

process_picked_wt() { # resume the durable phase recorded on a todo card
  local file=$1 base_branch=$2 id
  if [[ $(fm_get "$file" resume_phase "") == resolve ]]; then
    id=$(fm_get "$file" id "?")
    fm_set "$file" resume_phase ""
    process_resolve_wt "$file" "$base_branch" "kanban/$id" "$KB/wt/$id" ""
  else
    process_card_wt "$file" "$base_branch"
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
    jobs_max=$(fm_get "$KB/KANBAN.md" jobs "$DEFAULT_JOBS")
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
  local lock=$KB/.lock lockfile=$KB/.dispatcher.lock lock_owner=$KB/.dispatcher.owner.$$ lock_pid=""
  if [[ -f $lock ]]; then lock_pid=$(cat "$lock" 2>/dev/null || true); fi
  if [[ -n $lock_pid ]] && kill -0 "$lock_pid" 2>/dev/null; then
    die "dispatcher already running (pid $lock_pid)"
  fi
  if [[ -d $lockfile ]]; then
    die "dispatcher lock is busy"
  elif [[ -f $lockfile ]]; then
    lock_pid=$(cat "$lockfile" 2>/dev/null || true)
    if [[ -n $lock_pid ]] && kill -0 "$lock_pid" 2>/dev/null; then
      die "dispatcher already running (pid $lock_pid)"
    fi
    [[ -n $lock_pid ]] || die "dispatcher lock is busy (owner is unknown)"
    rm -f "$lockfile"
  fi
  printf '%s\n' $$ >"$lock_owner"
  if ! ln "$lock_owner" "$lockfile" 2>/dev/null; then
    lock_pid=$(cat "$lockfile" 2>/dev/null || true)
    rm -f "$lock_owner"
    if [[ -n $lock_pid ]] && kill -0 "$lock_pid" 2>/dev/null; then
      die "dispatcher already running (pid $lock_pid)"
    fi
    die "dispatcher lock is busy"
  fi
  rm -f "$lock_owner"
  echo $$ >"$lock"
  trap "rm -f '$lock' '$lockfile' '$lock_owner'; rmdir '$KB/.merge.lock' 2>/dev/null || true" EXIT
  rmdir "$KB/.merge.lock" 2>/dev/null || true
  [[ -n ${KANBAN_WORKER_CMD:-} ]] || die "visible worker wrapper is required; run kanban-secretary.sh dispatch"
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
    echo "kanban: Jobs: $jobs_max (live from .git/kanban/KANBAN.md; edit jobs: to resize safely)"
  fi
  echo "[UNRESTRICTED] worker/reviewer permission policy: claude=$(claude_perm_flag) codex=$(codex_sandbox_flag)" >&2
  # Reclaim cards stranded by a crashed dispatcher (lock guarantees exclusivity).
  # resolving/blocked cards also fold back their leftover worktree/branch so
  # the card restarts clean on its next pickup instead of colliding with
  # `git worktree add -b` on a name that already exists.
  local orphan
  for orphan in "$KB"/doing/*.md; do
    if [[ -e $orphan ]]; then
      if [[ $(fm_get "$orphan" task_kind implementation) == operation ]]; then
        local orphan_title
        orphan_title=$(fm_get "$orphan" title "?")
        fm_set "$orphan" blocked_kind operation_unknown
        printf 'dispatcher stopped while an external operation was in flight; remote state may already have changed. Automatic replay is forbidden. Verify the external state before resume.\n' |
          append_history "$orphan" "blocked (operation outcome unknown)"
        move_card "$orphan" blocked >/dev/null
        echo "[$orphan_title] BLOCKED operation outcome unknown after dispatcher restart"
        notify_result blocked "$orphan_title"
      else
        move_card "$orphan" todo >/dev/null
      fi
    fi
  done
  for orphan in "$KB"/review/*.md; do
    if [[ -e $orphan ]]; then move_card "$orphan" todo >/dev/null; fi
  done
  for orphan in "$KB"/resolving/*.md; do
    if [[ -e $orphan ]]; then
      fm_set "$orphan" resume_phase resolve
      move_card "$orphan" todo >/dev/null
    fi
  done
  for orphan in "$KB"/blocked/*.md; do
    if [[ -e $orphan ]]; then
      # review-infra and explicit-dependency cards remain parked: the former
      # waits for `kanban resume`, while the latter is refreshed below and
      # returns to todo only after its declared dependency reaches done.
      # Review-infra worktree/branch/commits are the whole point of keeping them.
      # Worker-reported ordering blocks are still reclaimed on restart.
      case $(fm_get "$orphan" blocked_kind "") in
        review_infra|review_decision|dependency|user_input|scope_timebox|operation_unknown|main_branch_changed) continue ;;
      esac
      local bid
      bid=$(fm_get "$orphan" id "?")
      if [[ $bid != "?" ]]; then
        kanban_remove_worktree "$KB/wt/$bid"
        git -C "$ROOT" branch -q -D "kanban/$bid" 2>/dev/null || true
      fi
      move_card "$orphan" todo >/dev/null
    fi
  done

  mkdir -p "$KB/wt"
  local base_branch
  base_branch=$(git -C "$ROOT" symbolic-ref --short HEAD) ||
    die "detached HEAD; check out a branch first"
  local spawned=0
  while :; do
    refresh_dependency_cards
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
              local t task_kind
              t=$(fm_get "$f" title "?" 2>/dev/null || basename "$2")
              task_kind=$(fm_get "$f" task_kind implementation 2>/dev/null || echo implementation)
              if [[ $task_kind == operation ]]; then
                fm_set "$f" blocked_kind operation_unknown
                echo "dispatcher job stopped during an external operation (exit $st); remote state may already have changed. Automatic replay is forbidden." |
                  append_history "$f" "blocked (operation outcome unknown)"
                move_card "$f" blocked >/dev/null
                echo "[$t] CRASH exit=$st -> blocked (operation outcome unknown)"
                notify_result blocked "$t"
                continue
              fi
              if [[ $(fm_get "$f" merge_pending "") == 1 || $(fm_get "$f" resolve_merge_pending "") == 1 || $f == "$KB/resolving/"* ]]; then
                echo "dispatcher job stopped (exit $st); durable phase checkpoint is preserved for the next visible dispatch" |
                  append_history "$f" "dispatcher crash"
                echo "[$t] CRASH exit=$st -> checkpoint preserved"
                continue
              fi
              echo "job crashed unexpectedly (exit $st); see dispatcher output" | append_history "$f" "dispatcher crash"
              fail_card "$f" dispatcher >/dev/null
              echo "[$t] CRASH exit=$st -> failed"
            fi
          done
        fi
      }
      if [[ $(fm_get "$picked" task_kind implementation) == operation ]]; then
        ( merge_lock acquire
          trap 'st=$?; merge_lock release; job_crash_net "$st" "$picked"' EXIT
          process_card_seq "$picked" ) &
      elif [[ -n ${KANBAN_DEBUG:-} ]]; then
        ( exec 2>"$KB/wt/job.$(basename "$picked").trace"; set -x
          trap 'job_crash_net $? "$picked"; echo "JOB EXIT status=$?" >&2' EXIT
          process_picked_wt "$picked" "$base_branch" ) &
      else
        ( trap 'job_crash_net $? "$picked"' EXIT
          process_picked_wt "$picked" "$base_branch" ) &
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

cmd_resume() { # cmd_resume <id-substring> [-m decision] -> resume a supported parked card
  require_root
  local pat="" decision=""
  while [[ $# -gt 0 ]]; do
    case $1 in
      -m|--decision) [[ $# -ge 2 ]] || die "$1 requires a value"; decision=$2; shift 2 ;;
      -*) die "unknown option for kanban resume: $1" ;;
      *) [[ -z $pat ]] || die "unexpected argument: $1 (id is already set)"; pat=$1; shift ;;
    esac
  done
  [[ -n $pat ]] || die "usage: kanban resume <id> [-m decision]"
  local hits=("$KB"/blocked/*"$pat"*.md)
  [[ -e ${hits[0]} ]] || die "no blocked card matching '$pat'"
  [[ ${#hits[@]} -eq 1 ]] || die "'$pat' matches multiple blocked cards; use the full id"
  local file=${hits[0]} kind
  kind=$(fm_get "$file" blocked_kind "")
  case $kind in
    review_infra|review_decision|user_input|scope_timebox|main_branch_changed) ;;
    operation_unknown) die "operation outcome is unknown; verify remote state, then use: kanban operation $pat done|retry" ;;
    *) die "card is blocked (kind: ${kind:-unknown}); this block kind is not manually resumable" ;;
  esac

  fm_set "$file" review_infra_retries 0
  fm_set "$file" worker_infra_retries 0
  fm_set "$file" blocked_kind ""
  if [[ -n $decision ]]; then
    printf '%s\n' "$decision" | append_history "$file" "user decision"
  fi
  local id
  id=$(fm_get "$file" id "?")
  if git -C "$ROOT" show-ref --verify --quiet "refs/heads/kanban-resolve/$id"; then
    fm_set "$file" resume_phase resolve
  fi
  move_card "$file" todo >/dev/null
  echo "resumed $kind card to todo; run kanban-secretary.sh dispatch"
}

cmd_operation() { # cmd_operation <id-substring> <done|retry>
  require_root
  [[ $# -ge 2 ]] || die "usage: kanban operation <id> {done|retry}"
  local pat=$1 decision=$2
  [[ $decision == done || $decision == retry ]] || die "operation decision must be done or retry (got: $decision)"
  local hits=("$KB"/blocked/*"$pat"*.md)
  [[ -e ${hits[0]} ]] || die "no blocked card matching '$pat'"
  [[ ${#hits[@]} -eq 1 ]] || die "'$pat' matches multiple blocked cards; use the full id"
  local file=${hits[0]} title
  [[ $(fm_get "$file" blocked_kind "") == operation_unknown ]] || die "card is not blocked by an unknown operation outcome"
  title=$(fm_get "$file" title "?")
  if [[ $decision == done ]]; then
    echo "external state verified: operation completed; the operation was not replayed" | append_history "$file" "operation resolved"
    move_card "$file" done >/dev/null
    file=$KB/done/$(basename "$file")
    fm_set "$file" blocked_kind ""
    notify_result done "$title"
    echo "operation marked done without replay"
  else
    fm_set "$file" blocked_kind ""
    echo "external state verified: operation may be retried explicitly" | append_history "$file" "operation resolved"
    move_card "$file" todo >/dev/null
    echo "operation queued for one explicit retry; run kanban-secretary.sh dispatch"
  fi
}

case ${1:-} in
  init) shift; cmd_init "$@" ;;
  migrate) shift; cmd_migrate "$@" ;;
  add) shift; cmd_add "$@" ;;
  ready) shift; cmd_ready "$@" ;;
  remove) shift; cmd_remove "$@" ;;
  config) shift; cmd_config "$@" ;;
  list|ls) cmd_list ;;
  show) shift; cmd_show "${1:?usage: kanban show <id>}" ;;
  inspect) shift; cmd_inspect "$@" ;;
  run) shift || true; cmd_run "$@" ;;
  resume) shift; cmd_resume "$@" ;;
  operation) shift; cmd_operation "$@" ;;
  projects) shift; python3 "$REGISTRY_CLI" projects "$@" ;;
  send) shift; python3 "$REGISTRY_CLI" send "$@" ;;
  --version) cat "$VERSION_FILE" ;;
  version) cmd_version ;;
  install) cmd_install ;;
  update) cmd_update ;;
  uninstall) cmd_uninstall ;;
  *) die "usage: kanban {init|migrate|add|ready [--check] <id>|remove <backlog-or-todo-id>|config set <key> <value>|list|show|inspect|run [--once] [-j N]|resume <id> [-m decision]|operation <id> {done|retry}|projects {add|list|show|update|remove}|send <alias> \"title\"|install|update|uninstall|version|--version}" ;;
esac
