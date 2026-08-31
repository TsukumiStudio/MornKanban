---
backend_order: claude codex
default_backend: claude
default_model: sonnet
reviewer: claude
review_model: sonnet
threshold: 80
max_attempts: 3
jobs: 2
claude_perms: acceptEdits
codex_sandbox: workspace-write
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

- 全カード投入後、対話エージェントが新しい Herdr ペインで以下を起動する:

  KANBAN_WORKER_CMD=/Users/matsufriends/git/MornKanban/herdr-agent-worker.sh KANBAN_REVIEW_CMD='env KANBAN_HERDR_ROLE=reviewer /Users/matsufriends/git/MornKanban/herdr-agent-worker.sh' kanban run -j 2; exit

- ヘッドレスワーカー (claude -p 直叩き) は禁止。ラッパー経由の可視エージェントのみ
- failed カードはユーザーへ即報告する
