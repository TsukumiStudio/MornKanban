---
backend_order: claude codex
default_backend: claude
default_model: sonnet
reviewer: claude
review_model: sonnet
threshold: 80
max_attempts: 3
jobs: 2
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

- ファイル境界で分割し、同一ファイルを触るカードは同時に投入しない
- 並列ワーカーは互いの成果物を見られない。API/DOM のインターフェース契約を
  各カード本文に明記し自己完結させる
- 完了条件と検証コマンド (python3 -m py_compile / node --check 等) を必ず書く

## ディスパッチャ運用

- 秘書開始時に `/Users/matsufriends/git/MornKanban/kanban-secretary.sh bootstrap "$PWD"` を実行し、
  current Herdr pane を実測して自分を通知先に登録する。プロンプトだけから Herdr の有無を推測しない
- 全カード投入後、対話エージェントは以下を実行する:

  /Users/matsufriends/git/MornKanban/kanban-secretary.sh dispatch "$PWD"

- ヘルパーは別の Herdr dispatcher pane を作り、worker / reviewer / notify の3変数を必ず束ねる
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
- カード間に依存がある場合 (完了条件が他カードの成果物を要る等) は同時投入せず、
  依存元の merge 後に次のバッチとして投入する。並列カードは互いの成果物を見られない
