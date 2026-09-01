---
backend_order: claude codex
default_backend: claude
default_model: sonnet
reviewer: claude
review_model: sonnet
threshold: 80
max_attempts: 3
resolve_max_attempts: 2
review_infra_max_retries: 2
review_infra_backoff_seconds: 2
review_enabled: true
jobs: 6
diagnosis_target_minutes: 5
diagnosis_max_minutes: 10
claude_perms: bypassPermissions
codex_sandbox: danger-full-access
codex_full_bypass: true
codex_approval: never
---

# このプロジェクトのカンバン運用ポリシー

## プロジェクト概要

MornKanban 本体。bash 3.2 互換の kanban.sh と Herdr 統合スクリプト群、
および誰でもセットアップできるローカル Web GUI (`kanban-gui.sh` + `gui/`)。
GUI は python3 標準ライブラリのみ (pip 不可)、フロントは素の HTML/CSS/JS。

## エージェント・モデル構成

- 上位モデル (fable / opus) は秘書・設計役のみ。ワーカー/レビュワーは sonnet 既定
- 設計・難所のカードだけ -m opus 等に上げ、理由をカードに書く

## カードの切り方

- 通常カードは `--type` / `--size` / `--goal` / 1個以上の `--ac` / `--scope` で構造化し、
  `kanban ready --check <id>` 後に `kanban ready <id>` でbacklogからReadyへ移す
- 必要な文脈・対象外・検証コマンドは `--context` / `--out-of-scope` / `--verify` へ分離し、会話だけに残さない
- 秘書はファイル競合や依存順序を判断せず、自己完結情報が揃ったカードを直ちに投入する
- 同一ファイルを触るカードも投入を止めない。競合・順序はworker / resolverが実行時に解決する
- 並列ワーカーは互いの成果物を見られない。API/DOM のインターフェース契約を
  各カード本文に明記し自己完結させる
- 完了条件と検証コマンド (python3 -m py_compile / node --check 等) を必ず書く

## 調査カード

- 調査・診断だけの依頼は `--diagnose` でread-onlyカードにし、5分で結論をまとめ、最大10分で止める
- 成果は証拠、原因候補、不確実性、次に切る小さな実装カード。修正や周辺改善を同じカードへ追加しない
- 修正は診断後の別カード。ユーザーが診断と修正を同時に明示した場合だけ通常の実装カードにする
- 最大時間に収まらない場合は途中証拠をHistoryへ残し、`BLOCKED: scope/timebox` で戻す

## ディスパッチャ運用

- 秘書開始時に `/Users/matsufriends/git/MornKanban/kanban-secretary.sh bootstrap "$PWD"` を実行し、
  current Herdr pane を実測して自分を通知先に登録する。プロンプトだけから Herdr の有無を推測しない
- 全カード投入後、対話エージェントは以下を実行する:

  /Users/matsufriends/git/MornKanban/kanban-secretary.sh dispatch "$PWD"

- ヘルパーは別の Herdr dispatcher pane を作り、worker / reviewer / resolver / operator / notify を必ず束ねる
- ヘッドレスワーカー (claude -p / codex exec 直叩き) は禁止。bare `kanban run` へ置き換えない
- current Herdr pane を確認できない場合は停止・報告する。ヘッドレスへ勝手にフォールバックしない
- failed カードはユーザーへ即報告する
- 検証も委譲する: 実装カードのマージ後に検証カードを切る。対話エージェントは実装も検証も手を動かさない

## 検証カードの制約 (2026-08-31 追記)

- 通常のワーカーはブラウザ操作ツール (claude-in-chrome 等) を**使わない**。検証は curl / CLI レベルで行う
- **ブラウザ役は同時に1エージェントのみ**: ウェブ経由の確認が必要なときは専用の「ブラウザ検証カード」を切り、
  `kanban-secretary.sh dispatch --once` で**単独実行**する。実行中は対話エージェントを含む他の全エージェントがブラウザツールに
  触れない (Chrome 拡張は同時に1クライアントしか安定して扱えないため)
- レポートは段階書き込み。起動したプロセスは必ず kill してから完了とする
- ブラウザ検証カードの前提: Claude in Chrome 拡張で localhost / 127.0.0.1 への
  アクセスを事前許可しておくこと (未許可だと権限ダイアログでワーカーが停止する)
- カード間の依存や競合は秘書が事前調整しない。workerが依存未完了を検出した場合は
  blockedとして戻し、dispatcher / resolverが正式な状態遷移で処理する

## 並列数

- このプロジェクトの既定は `jobs: 6`
- `-j` や `KANBAN_JOBS` を指定せず起動したdispatcherは、この値を実行中も再読込する
- 増加時は空き枠へ即時投入し、減少時は実行中jobを止めず、新規投入だけを抑える
