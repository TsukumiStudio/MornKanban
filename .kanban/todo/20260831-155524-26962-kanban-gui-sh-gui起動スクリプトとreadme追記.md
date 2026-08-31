---
id: 20260831-155524-26962
title: kanban-gui.sh: GUI起動スクリプトとREADME追記
backend: claude
model: sonnet
threshold: 80
max_attempts: 3
attempts: 0
created: 2026-08-31T15:55:24
---

## Task

## 目的
1. リポジトリ直下に `kanban-gui.sh` を新規作成 (実行権付与)。
2. README.md に「## GUI」節を追記する。

## kanban-gui.sh の仕様
- bash 3.2 互換、set -euo pipefail
- REPO は自身のあるディレクトリ (cd "$(dirname "$0")" && pwd)
- python3 が無ければエラー終了。gui/server.py が無ければエラー終了
- 環境変数 MORNKANBAN_GUI_PORT (既定 8765) を尊重
- HERDR_ENV が 1 でなければ「Herdr ペイン内での起動を推奨 (秘書/ディスパッチャ起動ボタンが使えない)」と警告して続行
- (sleep 1 && open "http://127.0.0.1:$PORT") をバックグラウンドで仕込む (open コマンドがある場合のみ)
- exec で MORNKANBAN_GUI_PORT を渡しつつ python3 "$REPO/gui/server.py" をフォアグラウンド実行

## README の「## GUI」節 (## Setup 節の直後に挿入)
- 内容: git clone → Herdr のペインで ./kanban-gui.sh → ブラウザが開く → 画面から
  プロジェクト追加 / kanban init / ポリシー編集 / カード追加 / 秘書起動 / ディスパッチャ起動が
  ポチポチでできる、という説明を簡潔な英語で 5〜8 行程度
- 既存の他の節は変更しない

## 完了条件・検証
- bash -n kanban-gui.sh が通る、test -x kanban-gui.sh が真
- grep -q '## GUI' README.md

## History
